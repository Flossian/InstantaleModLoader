# -*- coding: utf-8 -*-
r"""建てられる施設の表。種類・等級・街の規模と、値段と売上の式。

数字はここにだけ置く。`facility_investment.py` は式を呼ぶだけで、値を持たない。

## 語彙はゲームのもの

| 何 | 語彙 | 出どころ |
| --- | --- | --- |
| 種類 | `facility_type`（`inn` / `general_store` / `specialty_shop` / `blacksmith` / `training_facility` / `colosseum`） | 実セーブの施設。主の `job` も同じ語（GAME.md §2.13.1） |
| 等級 | `tier`（`basic` / `standard` / `advanced`） | 実セーブの施設 312 件がこの3語だけ |
| 規模 | `area["size"]`（`village` / `town` / `city`） | `324_` の `AREA_SIZES`。`dungeon` は街ではない |

## どこに何軒まで建つか

**上限は種類ごとではなく、その街に建てられる合計の軒数**（本人の指定、2026-09-14）。
数えるのは**この MOD で建てた分だけ**で、ゲームが最初から置いた施設は数えない
（数えると宿屋も雑貨店も最初から在る村では何も建たなくなる）。

    村 1軒 / 町 2軒 / 都市 3軒     ← 種類を問わない合計（SIZE_SLOTS）

種類ごとにあるのは**最低の規模**だけ（`min_size`）。
道場と闘技場は町から（村には建たない）。実セーブ56街でも村には1軒も無かった（DOC.md §3.1）。

上限は**その街に既にある同種の施設も数に入れる**。宿屋が1軒ある村には建てられない。

## 値段と売上

    費用       = 基礎額   × 等級の倍率 × 規模の倍率
    1日の売上  = 基礎売上 × 等級の倍率 × 規模の倍率

規模の倍率を両方に掛けるので、都市は高いが儲かり、村は安いが細い。
等級は費用より売上の伸びを小さくしてある（上の等級ほど回収が遅い ＝ 見栄の値段）。
"""

import hashlib

# ---------------------------------------------------------------- 規模
SIZES = ("village", "town", "city")
SIZE_LABEL = {"village": "村", "town": "町", "city": "都市"}
SIZE_ALIAS = {"dungeons": "dungeon"}
#: 費用に掛ける。
SIZE_COST = {"village": 0.6, "town": 1.0, "city": 1.8}
#: 売上に掛ける。
SIZE_INCOME = {"village": 0.5, "town": 1.0, "city": 2.0}
#: その街に建てられる合計の軒数（種類を問わない。本人の指定、2026-09-14）。
SIZE_SLOTS = {"village": 1, "town": 2, "city": 3}

# ---------------------------------------------------------------- 等級
TIERS = ("basic", "standard", "advanced")
TIER_LABEL = {"basic": "並", "standard": "上等", "advanced": "最上"}
TIER_COST = {"basic": 1.0, "standard": 1.8, "advanced": 3.2}
TIER_INCOME = {"basic": 1.0, "standard": 1.5, "advanced": 2.2}

# ---------------------------------------------------------------- 種類
#: `enabled` が偽の種類は窓口に出さない（中身の経路が未確認のもの。DOC.md §3.3）。
#: `plain` が真の種類は、中に立っている間だけ素データの写しを置いてもらう
#: （`modfacility.register(plain=...)`）。ゲームが施設 id で素データを引く経路
#: （売買・闘技場の試合）のため。宿屋は要らない（実体で足りた。DOC.md §3.2 の11回目）。
KINDS = {
    "inn": {
        "label": "宿屋",
        "enabled": True,
        "plain": False,
        "min_size": "village",
        "cost": 20000,
        "income": 60,
        "names": {
            "basic": ("{area}の木賃宿", "旅人の寝床", "灯火亭"),
            "standard": ("{area}の宿「白鳩」", "青銅の枕亭", "旅籠・月影"),
            "advanced": ("{area}迎賓館", "金羊亭", "星辰の間"),
        },
        "descriptions": {
            "basic": "出資者の名で建った小さな宿。寝台は硬いが屋根は雨を通さない。",
            "standard": "出資者の名で建った中規模の宿。個室があり、旅の商人がよく使う。",
            "advanced": "出資者の名で建った街いちばんの宿。貴人の逗留にも耐える造り。",
        },
        "keeper_job": "inn",
        "keeper_role": "宿の主人",
    },
    "general_store": {
        "label": "雑貨店",
        "enabled": True,
        # 売買（`shopping_start_method_1`）は施設 id で素データを引く（GAME.md §2.28）。
        "plain": True,
        "min_size": "village",
        "cost": 25000,
        "income": 70,
        "names": {
            "basic": ("{area}の露店", "軒先の雑貨屋", "三日月商店"),
            "standard": ("{area}商会", "旅装の店「北斗」", "鈴掛け商店"),
            "advanced": ("{area}大商会", "黄金天秤堂", "万象商館"),
        },
        "descriptions": {
            "basic": "出資者の名で開いた小さな雑貨屋。旅の要りようが一通り揃う。",
            "standard": "出資者の名で開いた商店。棚には近隣の街から集めた品が並ぶ。",
            "advanced": "出資者の名で開いた大商会。遠国の品まで扱う、街いちばんの店。",
        },
        "keeper_job": "general_store",
        "keeper_role": "店主",
    },
    "specialty_shop": {
        "label": "専門店",
        "enabled": True,
        "plain": True,
        "min_size": "village",
        "cost": 45000,
        "income": 110,
        "names": {
            "basic": ("{area}の薬種店", "香草と粉の店", "小瓶堂"),
            "standard": ("{area}特産店", "藍染めの店「翠」", "銀糸工房"),
            "advanced": ("{area}名品店", "琥珀館", "七宝の間"),
        },
        "descriptions": {
            "basic": "出資者の名で開いた専門店。扱う品は狭いが、質は確かだ。",
            "standard": "出資者の名で開いた専門店。目当ての品を求めて客が来る。",
            "advanced": "出資者の名で開いた名品店。値は張るが、ここにしか無い品がある。",
        },
        "keeper_job": "specialty_shop",
        "keeper_role": "店主",
    },
    "blacksmith": {
        "label": "鍛冶屋",
        "enabled": True,
        "plain": True,
        "min_size": "village",
        "cost": 40000,
        "income": 100,
        "names": {
            "basic": ("{area}の鍛冶場", "槌音の工房", "鉄床小屋"),
            "standard": ("{area}鍛冶工房", "赤熱の炉", "鋼響堂"),
            "advanced": ("{area}名工房", "白刃の炉", "竜鱗鍛冶"),
        },
        "descriptions": {
            "basic": "出資者の名で開いた鍛冶場。修理と打ち直しで日銭を稼ぐ。",
            "standard": "出資者の名で開いた鍛冶工房。注文の武具も引き受ける。",
            "advanced": "出資者の名で開いた名工房。名のある打ち手を招いてある。",
        },
        "keeper_job": "blacksmith",
        "keeper_role": "鍛冶師",
    },
    "training_facility": {
        "label": "道場",
        "enabled": True,
        # 訓練の入口はゲームが `facility_type` から出す（宿屋・闘技場と同じ）ので、
        # こちらが `DisplayTrainingChoice(app, training_type)` を組む場面は無い。
        # その先が施設 id で素データを引くなら写しが要る（DOC.md §3.2 の18回目）。
        "plain": True,
        "min_size": "town",
        "cost": 35000,
        "income": 80,
        "names": {
            "basic": ("{area}の鍛錬場", "土間道場"),
            "standard": ("{area}武芸所", "鉄心道場"),
            "advanced": ("{area}総合武練院", "白刃殿"),
        },
        "descriptions": {
            "basic": "出資者の名で開いた鍛錬場。板の間と木剣だけの道場。",
            "standard": "出資者の名で開いた武芸所。師範が常駐し、門弟が通う。",
            "advanced": "出資者の名で開いた武練院。名のある師範を招いてある。",
        },
        "keeper_job": "training_facility",
        "keeper_role": "師範",
    },
    "colosseum": {
        "label": "闘技場",
        "plain": True,
        # 試合（`ColosseumMatchStart.method`）は施設の `config`
        # （`current_phase` / `enemy_data`）を `world_dict` から施設 id で引く（実機 2026-09-13）。
        "enabled": True,
        "min_size": "town",
        "cost": 120000,
        "income": 300,
        "names": {
            "basic": ("{area}の土俵場", "砂塵の闘技場"),
            "standard": ("{area}円形闘技場", "鉄環の闘技場"),
            "advanced": ("{area}大闘技場", "黄金の円環"),
        },
        "descriptions": {
            "basic": "出資者の名で開いた小さな闘技場。柵と砂と観客席だけの興行場。",
            "standard": "出資者の名で開いた円形闘技場。街の祭日には満席になる。",
            "advanced": "出資者の名で開いた大闘技場。近隣の街からも客が来る。",
        },
        "keeper_job": "colosseum",
        "keeper_role": "興行主",
    },
}

#: 窓口に並べる順。安いものから。
KIND_ORDER = ("inn", "general_store", "blacksmith", "specialty_shop",
              "training_facility", "colosseum")

#: 窓口に出す設置条件の一文（本人の指定、2026-09-14）。
#: 建てられないときに「いま建てられるものは無い」だけだと、
#: 規模が足りないのか軒数が埋まっているのかが分からない。
REQUIREMENT_MIN_SIZE = "{label}: {min_size}から（{size}には建たない）"
SLOTS_TEXT = "この街に建てられるのは合計{slots}軒まで（いま{count}軒）"
SLOTS_FULL_TEXT = "この街に建てられるのは合計{slots}軒まで（いま{count}軒。空きが無い）"


# ---------------------------------------------------------------- 主人
#: `category` は実セーブの NPC 541 人が使っている10語のうち、主人に据えて不自然でないもの。
#: 6人だと4軒目あたりで名前が重なる（実機 2026-09-14：闘技場と道場がどちらもトビアスになった）。
#: 数を増やしたうえで、`keeper_choice` が**その世界で使っていない名前**まで送る。
KEEPER_POOL = (
    ("ガレン", "middle-aged man", "低い声で手短に話す", "無口だが客を見る目は確か"),
    ("マルタ", "middle-aged woman", "早口で世話焼き", "客を家族のように扱い、帳簿にも厳しい"),
    ("トビアス", "young man", "丁寧で少し堅い", "出資者に恩義を感じていて、真面目に切り盛りする"),
    ("リーゼ", "young woman", "明るく歯切れがよい", "商売の勘が鋭く、客の懐を読むのがうまい"),
    ("オズワルド", "old man", "ゆっくりと諭すように話す", "この街で長く商いをしてきた古株"),
    ("ヘルガ", "old woman", "短く、ときどき皮肉を混ぜる", "若い頃は行商で各地を回っていた"),
    ("ドルヴァ", "middle-aged man", "訛りが強く、声が大きい", "腕っぷしで若い頃を渡ってきた"),
    ("シルケ", "young woman", "落ち着いた小声で話す", "数字に細かく、無駄を嫌う"),
    ("ベネデット", "old man", "遠回しで、たとえ話が多い", "各地の商いを見てきた隠居"),
    ("ナジャ", "middle-aged woman", "きびきびと短く話す", "人を使うのがうまく、顔が広い"),
    ("クヴィン", "young man", "早口で人懐こい", "新しい商売の話に目がない"),
    ("エルナ", "old woman", "ゆっくり、間を置いて話す", "この土地の古い決まりに通じている"),
)

#: 主人のレベル。詳細生成が掛け算に使うので None にしない（`keeper_fields`）。
KEEPER_LEVEL = 5
#: 主人がプレイヤーに持つ好感度の初期値。40 は「多少の好意がある」の段（GAME.md §2.25.1）。
#: 会話の頼み文には `affinity` から組んだ文が毎回載るので、初対面の「警戒心がある」のままだと
#: 出資者を警戒する主人になる（実機 2026-09-13 の指摘）。
KEEPER_AFFINITY = 40

KEEPER_PROFILE = (
    "{area}の{facility}を任されている{role}。"
    "建てたのは出資者の{investor}で、売上を預かり、{investor}が訪ねてきたときに渡している。{note}。"
)
#: 会話の頼み文に足す素性（`modnpc` の `notes`）。相手が出資者本人だと毎回伝える。
KEEPER_NOTES = (
    "いま話している{investor}は、この{facility}を建てた出資者で、あなたにとっては雇い主にあたる。"
    "売上を預かって渡す相手であり、出資者として礼を尽くして接する。"
)
#: 闘技場の相手を作る頼み文に足す一文。ゲームの頼み文は世界観・エリア・施設・ランクだけで、
#: 前に出た相手を載せないため、同じ施設では毎回同じ人物に収束した（実機 2026-09-13。DOC.md §3.2 の14回目）。
ARENA_VARIETY_NOTE = (
    "この闘技場には既に {names} が出場している。"
    "名前も出自も戦い方もこれらとは重ならない、別の闘士を作ること。"
)
#: 名前そのものに読点を含むことがある（「黄金の断罪者、アウレリウス」）ので、
#: 並べるときは1人ずつ括る（実機 2026-09-13。読点で繋いだら切れ目が読めなかった）。
ARENA_NAME_WRAP = "「{}」"


def _index(seed, count):
    """鍵から 0..count-1 の1つ。同じ鍵からは同じ数。"""
    if count <= 0:
        return 0
    digest = hashlib.sha1(str(seed).encode("utf-8")).hexdigest()
    return int(digest[:8], 16) % count


def _pick(seed, options):
    """同じ鍵からは同じものを選ぶ（建て直しても名前が変わらない）。"""
    if not options:
        return None
    return options[_index(seed, len(options))]


# ---------------------------------------------------------------- 式
def _base(value, fallback):
    """設定から来た基礎額。読めない値（空・負・数でない）なら表の値。"""
    try:
        number = int(value)
    except (TypeError, ValueError):
        return fallback
    return number if number >= 0 else fallback


def normalize_size(value):
    """`area["size"]` を街の規模に均す。街でなければ None。"""
    if not isinstance(value, str) or not value:
        return None
    size = SIZE_ALIAS.get(value.strip().lower(), value.strip().lower())
    return size if size in SIZES else None


def kind_of(kind):
    """種類の表。無ければ None。"""
    return KINDS.get(str(kind))


def enabled_kinds():
    return [k for k in KIND_ORDER if KINDS[k].get("enabled")]


def needs_plain(kind):
    """中に立っている間だけ素データの写しを置く種類か。"""
    spec = kind_of(kind)
    return bool(spec and spec.get("plain"))


def floor_size(kind, min_size=None):
    """その種類を建てられる最低の規模。`min_size` は設定からの上書き（読めなければ表の値）。"""
    spec = kind_of(kind)
    if spec is None:
        return None
    return normalize_size(min_size) or spec["min_size"]


def allowed(kind, size, min_size=None):
    """その規模の街に建ててよい種類か（軒数は見ない）。

    `min_size` は設定からの上書き（`915_` の `MIN_SIZE_<種類>`）。
    """
    spec = kind_of(kind)
    size = normalize_size(size)
    if spec is None or size is None:
        return False
    return SIZES.index(size) >= SIZES.index(floor_size(kind, min_size))


def slots(size):
    """その規模の街に建てられる合計の軒数。種類は問わない。街でなければ 0。"""
    size = normalize_size(size)
    return int(SIZE_SLOTS.get(size, 0)) if size else 0


def cost_of(kind, tier, size, scale=100, base=None):
    """建設費。`scale` は % で、設定から来る。

    `base` は種類ごとの基礎額の上書き（`915_` の `COST_<種類>`。読めなければ表の値）。
    等級と街の規模の倍率はその上に掛かるので、`base` は**並・町**での額と同じ。
    """
    spec = kind_of(kind)
    size = normalize_size(size)
    if spec is None or size is None or tier not in TIERS:
        return None
    raw = _base(base, spec["cost"]) * TIER_COST[tier] * SIZE_COST[size] * (scale / 100.0)
    return int(round(raw / 100.0)) * 100     # 100G 単位に丸める


def income_per_day(kind, tier, size, scale=100, base=None):
    """1日の売上。`base` は種類ごとの基礎額の上書き（`915_` の `INCOME_<種類>`）。"""
    spec = kind_of(kind)
    size = normalize_size(size)
    if spec is None or size is None or tier not in TIERS:
        return 0
    return int(round(_base(base, spec["income"]) * TIER_INCOME[tier] * SIZE_INCOME[size]
                     * (scale / 100.0)))


def facility_name(kind, tier, area_name, seed):
    spec = kind_of(kind)
    if spec is None:
        return ""
    template = _pick(seed, spec["names"].get(tier) or ())
    return (template or spec["label"]).format(area=area_name or "")


def description_of(kind, tier):
    spec = kind_of(kind)
    if spec is None:
        return ""
    return spec["descriptions"].get(tier) or ""


def requirement_text(kind, size, min_size=None):
    """その種類がその規模の街に建たないときの一文。建つなら空。

    軒数はもう種類ごとではないので（`slots_text`）、ここが言うのは最低の規模だけ。
    """
    spec = kind_of(kind)
    size = normalize_size(size)
    if spec is None or size is None or allowed(kind, size, min_size):
        return ""
    floor = floor_size(kind, min_size)
    return REQUIREMENT_MIN_SIZE.format(
        label=spec["label"], size=SIZE_LABEL.get(size, size),
        min_size=SIZE_LABEL.get(floor, floor))


def slots_text(size, count=0):
    """その街の合計の軒数と、いま建っている数。街でなければ空。"""
    size = normalize_size(size)
    if not size:
        return ""
    limit = slots(size)
    count = count if isinstance(count, int) and count > 0 else 0
    template = SLOTS_FULL_TEXT if count >= limit else SLOTS_TEXT
    return template.format(slots=limit, count=count)


def keeper_choice(seed, taken=()):
    """主人の候補を1人。`taken` に在る名前は飛ばす（同じ世界で名前を重ねない）。

    鍵から始めて並びを順に送るだけなので、**同じ鍵と同じ `taken` からは同じ人**になる。
    全部使われていたら鍵の人をそのまま返す（重なるのは仕方ない）。
    """
    taken = set(str(name) for name in (taken or ()) if name)
    start = _index(seed, len(KEEPER_POOL))
    for step in range(len(KEEPER_POOL)):
        candidate = KEEPER_POOL[(start + step) % len(KEEPER_POOL)]
        if candidate[0] not in taken:
            return candidate
    return KEEPER_POOL[start]


def keeper_by_name(name):
    """名前から候補を引く。無ければ None。"""
    for candidate in KEEPER_POOL:
        if candidate[0] == str(name):
            return candidate
    return None


def keeper_relationship():
    """主人がプレイヤーに持つ関係の初期値（実セーブの `relationship["player"]` と同じ4鍵）。

    `affinity_text` は会話のたびにゲームが `affinity` から組み直す（GAME.md §2.25.1）ので、
    ここに書く文は最初の会話までのつなぎ。
    """
    return {"player": {"affinity": KEEPER_AFFINITY, "affinity_text": "多少の好意がある",
                       "relationship": ["出資者"], "conversation_count": 0}}


def keeper_notes(investor, facility_name_):
    return KEEPER_NOTES.format(investor=investor or "出資者", facility=facility_name_ or "施設")


def arena_variety_note(names):
    """既に出た闘士の名前を並べた一文。名前が無ければ空。"""
    names = [str(n).strip() for n in names if isinstance(n, str) and n.strip()]
    if not names:
        return ""
    return ARENA_VARIETY_NOTE.format(
        names="".join(ARENA_NAME_WRAP.format(name) for name in names[-8:]))


#: 老若男女の語。実セーブの NPC が使う語のうち人間の6語。
#: LLM の答えがこれ以外なら表の値へ落とす。
KEEPER_CATEGORIES = ("young man", "young woman", "middle-aged man",
                     "middle-aged woman", "old man", "old woman")

#: 主人を作らせる頼み文。ゲーム自身の生成（`master_ai_npc_generater`）と同じ口調で、
#: こちらが要るのは4つ（名前・老若男女・話し方・人柄）と素性の文だけ。
#: 能力値・見た目・技はゲームの詳細生成が会話の直前に埋める（GAME.md §2.23）。
KEEPER_SYSTEM = (
    "あなたはダークファンタジーRPGのキャラクター生成AIだ。\n"
    "出資者の金で建った施設を任される主人を1人設定しろ。\n"
    "【出力要素】\n"
    "- name: 主人の名前。この世界の人名らしく、姓を付けず短く。\n"
    "- category: 老若男女のカテゴリ。次のどれか1つをそのまま書く: {categories}\n"
    "- speech_style: 話し方の癖を一文で。\n"
    "- personality: 性格と来歴を一文か二文で。\n"
    "- profile: 何者かを二文で。その施設を任されていること、"
    "売上を預かって出資者が訪ねてきたときに渡すことを含める。\n"
    "【その他】\n"
    "- 出資者に雇われた立場で、出資者には礼を尽くす。敵対的な人物にはしない。\n"
    "- 戦う相手ではない。強さや戦闘の話は書かない。\n"
)

KEEPER_ASK = (
    "【世界の情報】\n- 世界観: {world}\n"
    "【土地の情報】\n- 名前: {area}（{size}）\n"
    "【施設の情報】\n- 名前: {facility}\n- 種類: {kind}\n- 格: {tier}\n- 概要: {description}\n"
    "【出資者】\n- 名前: {investor}\n"
    "【生成する主人】\n- 立場: {role}\n"
)

#: 既にこの世界に居る主人の名前を避けさせる一文。
KEEPER_AVOID = "- 次の名前は既にこの世界の主人が使っている。避けること: {names}\n"


def keeper_prompt(kind, tier, area_name, size, facility_name_, world_overview,
                  investor, taken=()):
    """主人を作らせる頼み文。`(system, user)`。"""
    spec = kind_of(kind) or {}
    system = KEEPER_SYSTEM.format(categories=" / ".join(KEEPER_CATEGORIES))
    names = [str(name) for name in (taken or ()) if name]
    if names:
        system += KEEPER_AVOID.format(names="、".join(names))
    user = KEEPER_ASK.format(
        world=world_overview or "", area=area_name or "この街",
        size=SIZE_LABEL.get(normalize_size(size) or "", size or ""),
        facility=facility_name_ or spec.get("label", ""),
        kind=spec.get("label", str(kind)),
        tier=TIER_LABEL.get(tier, tier or ""),
        description=description_of(kind, tier), investor=investor or "出資者",
        role=spec.get("keeper_role", "主人"))
    return system, user


def keeper_fields_from(answer, kind, area_name, facility_name_, seed,
                      investor=None, taken=()):
    """LLM の答えを主人の素データに均す。読めない項目は表の人で埋める。

    名前が空・既に使われている・`category` が語彙の外、のどれかなら**その項目だけ**落とす。
    答え全体が読めなければ `None`（呼ぶ側が表へ降りる）。
    """
    if not isinstance(answer, dict):
        return None
    fallback = keeper_fields(kind, area_name, facility_name_, seed,
                             investor=investor, taken=taken)
    used = set(str(name) for name in (taken or ()) if name)

    def text(key, limit):
        value = answer.get(key)
        value = value.strip() if isinstance(value, str) else ""
        return value[:limit] if value else ""

    name = text("name", 24)
    if not name or name in used:
        return None                 # 名前が使えないなら表の人に任せる
    fields = dict(fallback)
    fields["name"] = name
    category = text("category", 32)
    if category in KEEPER_CATEGORIES:
        fields["category"] = category
    for key, limit in (("speech_style", 120), ("personality", 400), ("profile", 600)):
        value = text(key, limit)
        if value:
            fields[key] = value
    return fields


def keeper_fields(kind, area_name, facility_name_, seed, investor=None,
                  taken=(), name=None):
    """主人の素データ（`modnpc.register` の `fields`）。

    `investor` は出資者（プレイヤー）の名。
    `name` を渡すとその人で組む（帳簿に控えた主人。建て直しで名前が変わらない）。
    渡さなければ鍵から選び、`taken` に在る名前は飛ばす。
    """
    spec = kind_of(kind) or {}
    chosen = keeper_by_name(name) if name else None
    name, category, speech, note = chosen or keeper_choice(seed, taken)
    return {
        "name": name,
        "category": category,
        "job": spec.get("keeper_job") or str(kind),
        "profile": KEEPER_PROFILE.format(area=area_name or "この街",
                                         facility=facility_name_ or spec.get("label", ""),
                                         role=spec.get("keeper_role", "主人"),
                                         investor=investor or "出資者",
                                         note=note),
        "relationship": keeper_relationship(),
        "personality": note,
        "speech_style": speech,
        "look_description": None,
        "age": 20,
        # 詳細生成（会話の直前に本体が能力値を埋める）は `experience_level` を掛け算に使う。
        # None だと `calculate_attribute` が `int * None` で落ち、会話のスレッドが死んで
        # 画面が「…」のまま戻らない（実機 2026-09-13。DOC.md §3.2）。
        # 商人なので低め。戦う相手ではない。
        "experience_level": KEEPER_LEVEL,
    }
