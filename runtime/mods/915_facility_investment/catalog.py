# -*- coding: utf-8 -*-
r"""建てられる施設の表。種類・等級・街の規模と、値段と売上の式。

数字はここにだけ置く。`facility_investment.py` は式を呼ぶだけで、値を持たない。

## 語彙はゲームのもの

| 何 | 語彙 | 出どころ |
| --- | --- | --- |
| 種類 | `facility_type`（`inn` / `colosseum` / `training_facility`） | 実セーブの施設。主の `job` も同じ語（GAME.md §2.13.1） |
| 等級 | `tier`（`basic` / `standard` / `advanced`） | 実セーブの施設 312 件がこの3語だけ |
| 規模 | `area["size"]`（`village` / `town` / `city`） | `324_` の `AREA_SIZES`。`dungeon` は街ではない |

## どこに何軒まで建つか（実セーブ 6 世界・56 の街を数えて決めた。DOC.md §3.1）

    inn                村 0〜1 / 町 1〜2 / 都市 0〜2   → 村1・町2・都市3
    training_facility  村 0   / 町 0〜1 / 都市 0〜2   → 町1・都市2（村には建たない）
    colosseum          村 0   / 町 0〜1 / 都市 0〜1   → 町1・都市1（村には建たない）

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

# ---------------------------------------------------------------- 等級
TIERS = ("basic", "standard", "advanced")
TIER_LABEL = {"basic": "並", "standard": "上等", "advanced": "最上"}
TIER_COST = {"basic": 1.0, "standard": 1.8, "advanced": 3.2}
TIER_INCOME = {"basic": 1.0, "standard": 1.5, "advanced": 2.2}

# ---------------------------------------------------------------- 種類
#: `enabled` が偽の種類は窓口に出さない（中身の経路が未確認のもの。DOC.md §3.3）。
KINDS = {
    "inn": {
        "label": "宿屋",
        "enabled": True,
        "min_size": "village",
        "cap": {"village": 1, "town": 2, "city": 3},
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
    "training_facility": {
        "label": "道場",
        # `DisplayTrainingChoice(app, training_type)` の `training_type` の語彙が
        # まだ測れていない（DOC.md §3.3）。表には置くが窓口には出さない。
        "enabled": False,
        "min_size": "town",
        "cap": {"village": 0, "town": 1, "city": 2},
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
        "enabled": True,
        "min_size": "town",
        "cap": {"village": 0, "town": 1, "city": 1},
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

#: 窓口に並べる順。
KIND_ORDER = ("inn", "training_facility", "colosseum")


# ---------------------------------------------------------------- 主人
#: `category` は実セーブの NPC 541 人が使っている10語のうち、主人に据えて不自然でないもの。
KEEPER_POOL = (
    ("ガレン", "middle-aged man", "低い声で手短に話す", "無口だが客を見る目は確か"),
    ("マルタ", "middle-aged woman", "早口で世話焼き", "客を家族のように扱い、帳簿にも厳しい"),
    ("トビアス", "young man", "丁寧で少し堅い", "出資者に恩義を感じていて、真面目に切り盛りする"),
    ("リーゼ", "young woman", "明るく歯切れがよい", "商売の勘が鋭く、客の懐を読むのがうまい"),
    ("オズワルド", "old man", "ゆっくりと諭すように話す", "この街で長く商いをしてきた古株"),
    ("ヘルガ", "old woman", "短く、ときどき皮肉を混ぜる", "若い頃は行商で各地を回っていた"),
)

KEEPER_PROFILE = (
    "{area}の{facility}を任されている{role}。"
    "建てたのは出資者で、売上を預かり、訪ねてきたときに渡している。{note}。"
)


def _pick(seed, options):
    """同じ鍵からは同じものを選ぶ（建て直しても名前が変わらない）。"""
    if not options:
        return None
    digest = hashlib.sha1(str(seed).encode("utf-8")).hexdigest()
    return options[int(digest[:8], 16) % len(options)]


# ---------------------------------------------------------------- 式
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


def allowed(kind, size):
    """その規模の街に建ててよい種類か（軒数は見ない）。"""
    spec = kind_of(kind)
    size = normalize_size(size)
    if spec is None or size is None:
        return False
    return SIZES.index(size) >= SIZES.index(spec["min_size"])


def cap(kind, size):
    """その規模の街に建てられる上限の軒数（既にある分も含めて）。"""
    spec = kind_of(kind)
    size = normalize_size(size)
    if spec is None or size is None:
        return 0
    return int(spec["cap"].get(size, 0))


def cost_of(kind, tier, size, scale=100):
    """建設費。`scale` は % で、設定から来る。"""
    spec = kind_of(kind)
    size = normalize_size(size)
    if spec is None or size is None or tier not in TIERS:
        return None
    raw = spec["cost"] * TIER_COST[tier] * SIZE_COST[size] * (scale / 100.0)
    return int(round(raw / 100.0)) * 100     # 100G 単位に丸める


def income_per_day(kind, tier, size, scale=100):
    """1日の売上。"""
    spec = kind_of(kind)
    size = normalize_size(size)
    if spec is None or size is None or tier not in TIERS:
        return 0
    return int(round(spec["income"] * TIER_INCOME[tier] * SIZE_INCOME[size]
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


def keeper_fields(kind, area_name, facility_name_, seed):
    """主人の素データ（`modnpc.register` の `fields`）。"""
    spec = kind_of(kind) or {}
    name, category, speech, note = _pick(seed, KEEPER_POOL)
    return {
        "name": name,
        "category": category,
        "job": spec.get("keeper_job") or str(kind),
        "profile": KEEPER_PROFILE.format(area=area_name or "この街",
                                         facility=facility_name_ or spec.get("label", ""),
                                         role=spec.get("keeper_role", "主人"),
                                         note=note),
        "personality": note,
        "speech_style": speech,
        "look_description": None,
        "age": 20,
    }
