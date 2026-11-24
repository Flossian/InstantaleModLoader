# -*- coding: utf-8 -*-
"""人体改造の規則。ゲームには触らない（ゲーム抜きの検査で全部通せる）。

部位と枠・効果の量・料金と入院の日数・失敗の見込み・見立ての検算・控えの形・画面に出す行を持つ。
設定は読まない（必要な値は `Config` で受け取る。TECH.md §3.1.1.1）。
遊び方は DOC.md。
"""

import hashlib

from instantale_modloader import battle_effects

# ---------------------------------------------------------------- 部位
#: 部位はゲームの怪我（`body_parts`）と同じ6つ。並びは画面の左の列・右の列。
PARTS = ("head", "arms", "legs", "sanity", "body", "organs")
LEFT_PARTS = ("head", "arms", "legs")
RIGHT_PARTS = ("sanity", "body", "organs")
PART_NAMES = {"head": "頭", "arms": "腕", "legs": "脚", "body": "胴", "organs": "内臓", "sanity": "精神"}
#: 1部位の枠。0 は普通の枠、1 は違法の施術でだけ埋まる2つめ（過積載）。
SLOTS = 2

ABILITIES = ("strength", "dexterity", "constitution", "intelligence", "wisdom", "charisma")
ABILITY_NAMES = {"strength": "筋力", "dexterity": "器用", "constitution": "耐久",
                 "intelligence": "知力", "wisdom": "判断", "charisma": "魅力"}
#: 伸びる能力値と下がる能力値は、見立てが6つの能力値から品に合わせて選ぶ（同じ能力値にはしない）。
#: 下の2つは、見立てが答えなかった・重なったときの既定。代償は部位の働きから筋の通る能力値にする
#: （頭は感覚を機械に任せて判断が鈍る、内臓は臓器の負担で力が出ない）。
PART_COST = {"head": "wisdom", "arms": "dexterity", "legs": "constitution", "body": "dexterity",
             "organs": "strength", "sanity": "intelligence"}
#: 見立てが伸ばす能力値を答えなかったときの、部位ごとの能力値。
PART_BOOST = {"head": "intelligence", "arms": "strength", "legs": "dexterity", "body": "constitution",
              "organs": "constitution", "sanity": "wisdom"}

RARITIES = ("common", "rare", "magical", "epic", "legendary", "mythic")
#: 見立てに回す品の種類（ゲームの6種すべて）。クラフトで作った部品が防具や武器の種類になっても候補に乗せる。
#: 体に使えるかは種類ではなく見立てで決める。
ITEM_TYPES = ("material", "utility", "weapon", "wearable", "healing_item", "consumable")
#: 見立ての決まりの版。控えの見立てがこれと違えば見立て直す
#: （2 = 適合する品だけを候補に出す形、3 = 戦闘の代償も選ぶ形、4 = 禁制かどうかと、伸びる能力値を必ず2つ）。
APPRAISAL_RULE = 4
#: 1回の問い合わせで見立てる品の数。
BATCH_SIZE = 10
#: 所持品の欄のマス（GAME.md §2.13。位置 `grid_pos` は品の左下のマスで、y は下から）。
GRID_COLS, GRID_ROWS = 4, 6

#: 品の仕立ての具合（見立てが決める）。未加工の素材は量が1段下がり、失敗しやすい。
RAW, PROCESSED, PART = "raw", "processed", "part"
FINISHES = (RAW, PROCESSED, PART)
FINISH_NAMES = {RAW: "未加工", PROCESSED: "加工品", PART: "施術用の部品"}

# ---------------------------------------------------------------- 効果の量
#: 能力値の量 `(主, 副)`。段はレア度の並び（0〜5）に、未加工は -1（合法だけ）、限界施術は +2、
#: 禁制のアイテムは +3 を足したもの。段が1つ上がるごとに主と副の合計が1つ増える
#: （量が整数で小さいので、common〜magical の差は副で付ける）。上は +4 で止める。
BOOST_BY_TIER = ((1, 0), (1, 1), (2, 1), (2, 2), (3, 2), (3, 3), (4, 3), (4, 4), (4, 4))
OVERLIMIT_TIER_STEP = 2
FORBIDDEN_TIER_STEP = 3

#: 施術の方法。裏の事務所では、正規のアイテムに通常施術か限界施術（安全の上限を外した施術）を選べる
#: （画面ではボタンが2つ並ぶ）。禁制のアイテムは方法を選べず、裏の事務所でだけ受けられる。
#: 違法の改造になるのは限界施術と禁制のアイテム。
NORMAL, OVERLIMIT = "normal", "overlimit"
METHOD_NAMES = {NORMAL: "通常施術", OVERLIMIT: "限界施術"}
FORBIDDEN_WORD = "禁制"

#: 戦闘の効果（ローダの `battle_effects` の種類）。値は強さの3段（`411_` の表と同じ値）。
BATTLE_STRENGTH = {
    "critical": (10, 15, 20),
    "lifesteal": (6, 10, 15),
    "pierce": (20, 35, 50),
    "venom": (20, 30, 40),
    "endure": (50, 75, 100),
    "thorns": (20, 30, 40),
    "regen": (2, 3, 4),
    "evade": (10, 15, 20),
    "resist": (30, 50, 70),
    "desperate": (20, 30, 40),
    "reduce": (5, 8, 12),
}
#: 戦闘の効果の画面の名前。
BATTLE_NAMES = {"critical": "会心", "lifesteal": "吸収", "pierce": "貫通", "venom": "毒",
                "endure": "不屈", "thorns": "棘", "regen": "再生", "evade": "軽業", "resist": "耐性",
                "desperate": "火事場", "reduce": "軽減"}

#: この MOD だけの特殊効果。値は強さの3段。
#:   painblock  … 痛覚遮断。怪我で下がった能力値を、この割合（%）だけ戻す（怪我は残る）。
#:                痛みを感じないので、1サイクルごとに負傷の部位の怪我が `PAINBLOCK_WORSEN` 増える（傷が開く）
#:   sync       … 同調。改造の入っている部位1つにつき、受けるダメージをこの割合（%）減らす。6部位そろうと全能力値 +1
#:   selfrepair … 自己修復。1サイクルごとに、治療で治る怪我（100 未満）をこの量だけ戻す
PAINBLOCK, SYNC, SELFREPAIR = "painblock", "sync", "selfrepair"
OWN_STRENGTH = {PAINBLOCK: (50, 75, 100), SYNC: (1, 2, 3), SELFREPAIR: (1, 1, 2)}
SYNC_FULL_BONUS = 1
PAINBLOCK_WORSEN = 1
#: 違法の施術に必ず付く効果（腕と脚は暴走、ほかは侵食）。効果と負担を両方持つ。
#:   rampage … 暴走。会心がこの割合（%）付く。敵を倒すたびに、その部位の怪我 +1
#:   erosion … 侵食。1つめの伸びる能力値にこの量を足す。1サイクルごとに、その部位の怪我がこの量だけ増える
RAMPAGE, EROSION = "rampage", "erosion"
RAMPAGE_PARTS = ("arms", "legs")
RAMPAGE_CRITICAL = (25, 35, 50)
RAMPAGE_INJURY = 1
EROSION_BOOST = (1, 1, 2)
EROSION_INJURY = (1, 1, 2)
#: 暴走・侵食・傷が開くで溜まる怪我の上限。100（治療で治らない段階）の手前で止める。
WEAR_CAP = 99
#: 侵食・傷が開く・自己修復の1サイクル（日）。改造ごとに組み込んだ日から数える。
WEAR_CYCLE_DAYS = 10
#: 依頼が終わる（成否を問わない）と進むサイクルの数。依頼の間は暦が進まない（GAME.md §2.18）。
QUEST_CYCLES = 5
#: 刻みが30日ごとだった頃の控えの量 → 1サイクルの量（`normalize_bucket` が控えを直す）。
OLD_WEAR_AMOUNTS = {EROSION: {3: 1, 4: 1, 6: 2}, SELFREPAIR: {2: 1, 4: 1, 6: 2}}
#: 治療で治らない怪我（`lost`。GAME.md §2.19.1）。合法の施術が通ると、その部位の怪我を 0 に戻す。
LOST_FROM = 100

SPECIAL_NAMES = dict(BATTLE_NAMES, **{PAINBLOCK: "痛覚遮断", SYNC: "同調", SELFREPAIR: "自己修復",
                                      RAMPAGE: "暴走", EROSION: "侵食"})
#: 部位ごとの特殊効果の候補（見立てがこの中から1つ選ぶ。先頭が既定）。
PART_SPECIALS = {
    "head": ("critical", "resist", PAINBLOCK, SYNC),
    "arms": ("pierce", "lifesteal", "venom", SYNC),
    "legs": ("evade", "desperate", SYNC),
    "body": ("reduce", "thorns", SELFREPAIR, SYNC),
    "organs": ("regen", "endure", SELFREPAIR, SYNC),
    "sanity": ("resist", PAINBLOCK, SYNC),
}
#: 見立てに渡す特殊効果の説明。
SPECIAL_HINTS = {
    "critical": "急所を突きやすくなる", "resist": "毒や麻痺などを受けにくくなる", "pierce": "相手の守りを貫く",
    "lifesteal": "与えた傷から力を吸う", "venom": "当てた相手を毒で蝕む", "evade": "攻撃をかわしやすくなる",
    "desperate": "追い詰められるほど打たれ強くなる", "reduce": "受ける傷が浅くなる", "thorns": "受けた傷を相手に返す",
    "regen": "戦いの中で傷が塞がっていく", "endure": "倒れる一撃を一度だけ耐える",
    PAINBLOCK: "痛みを感じなくなり、怪我をしていても力が落ちない", SYNC: "ほかの改造と連動して全体を底上げする",
    SELFREPAIR: "日が経つと怪我がひとりでに治っていく",
}
#: 特殊効果の強さの段（レア度の並び）。合法は設定のレア度から付き、違法は必ず付く。
LEGAL_STEP = (0, 0, 0, 0, 1, 2)
ILLEGAL_STEP = (0, 0, 0, 1, 2, 2)

#: 代償の量の幅（下限, 上限）。施術のときに幅の中からランダムに決まる。0 なら下がらない。
#: 合法は common〜magical が 0〜1、epic 以上が 1〜2。違法は common〜magical が 1〜3、epic 以上が 2〜4。
COST_RANGE_LEGAL = ((0, 1), (1, 2))
COST_RANGE_ILLEGAL = ((1, 3), (2, 4))
COST_HIGH_FROM = 3      # epic

#: 戦闘の代償（ローダの `battle_effects` の `COST_KINDS`）。見立てが品に合うものを1つ選ぶ。
#: 合法の施術は能力値の代償か戦闘の代償の片方（見立てごとに決まる。`cost_side`）、違法の施術は両方が付く。
DRAWBACKS = ("fragile", "falter", "unmend", "backlash")
DRAWBACK_NAMES = {"fragile": "脆さ", "falter": "隙", "unmend": "回復阻害", "backlash": "反動"}
#: 見立てに渡す戦闘の代償の説明。
DRAWBACK_HINTS = {"fragile": "受ける傷が深くなる", "falter": "体が思うように動かず、攻撃が空を切ることがある",
                  "unmend": "傷を癒やす力が効きにくくなる", "backlash": "戦いの後に体が消耗する"}
#: 見立てが答えなかったときの、部位ごとの戦闘の代償。
#: 隙は手ごと空振りになって重いので、既定にするのは頭だけ（脚は脆さ）。
PART_DRAWBACK = {"head": "falter", "arms": "backlash", "legs": "fragile", "body": "fragile", "organs": "unmend",
                 "sanity": "backlash"}
#: 戦闘の代償の量（%）。合法・違法の common〜magical・違法の epic 以上の順。
#: `346_` の特質の代償（5% きざみで上限 15%、回復阻害は 15% きざみで上限 45%）に揃える。
#: 隙だけは引いた手の攻撃がまるごと空振りになり、同じ割合でもほかより重いので、3分の1ほどにする。
DRAWBACK_STRENGTH = {"fragile": (5, 10, 15), "falter": (2, 3, 5), "unmend": (15, 30, 45), "backlash": (5, 10, 15)}
#: この MOD の改造が足す代償の合計の上限（%）。ローダの窓口の上限（隙 50%）より手前で止める。
#: 窓口の上限を変えるとほかの MOD の隙まで変わるので、この MOD の分だけをここで止める。
DRAWBACK_CAPS = {"falter": 20}
#: 戦闘の代償と、それに相対する特殊効果。1つの改造の中では両方を付けない（`drawback_for`）。
#: 同調は軽減を足すので脆さと相対する。再生と吸収はローダの経路で回復阻害を受ける。
OPPOSED = {"fragile": ("reduce", "desperate", SYNC), "unmend": ("regen", "lifesteal")}
#: 代償が特殊効果と相対するときに差し替える順。隙は重いので最後。
DRAWBACK_FALLBACK = ("backlash", "fragile", "unmend", "falter")
#: 合法の施術で付く代償の側（`cost_side` の戻り）。
COST_ABILITY, COST_BATTLE = "ability", "battle"

# ---------------------------------------------------------------- 失敗
#: 失敗で部位に足す怪我（`injury`）。30 で `damaged`、60 で `disabled`（GAME.md §2.19.1）。
FAIL_INJURY = 30
#: 怪我で下がる能力値（ゲームの `check_body_damage` の部位ごとの倍率。GAME.md の体の部位の節）。
#: 失敗したときのことを画面に書くのに使う。
INJURY_ABILITIES = {"arms": ("strength", "dexterity"), "legs": ("constitution", "dexterity"),
                    "body": ("strength", "constitution"), "head": ("intelligence", "charisma"),
                    "organs": ("constitution",), "sanity": ("intelligence", "wisdom")}
#: 怪我が段階 `hurt` になる値（GAME.md §2.19.1）。画面の「負傷」の表示に使う。
HURT_FROM = 10
ILLEGAL_FAIL_INJURY = 60
#: 失敗の見込みを下げる能力値と、1つあたりの下げ幅（%）。11 が作成時の基準。
FAIL_ABILITIES = ("constitution", "wisdom")
BASE_SCORE = 11
FAIL_PER_POINT = 0.5
FAIL_CAP = 90.0
#: 合法の施術の失敗の見込み（%）。仕立ての具合とレア度で決まる。
FINISH_FAIL = {PART: 3.0, PROCESSED: 8.0, RAW: 15.0}
RARITY_FAIL = (0.0, 0.0, 1.0, 2.0, 4.0, 6.0)
FAIL_FLOOR = 1.0
ILLEGAL_FAIL_FLOOR = 10.0

#: 料金のレア度の倍率と、合法の施術の入院の日数（レア度の並び）。
RARITY_FEE = (1, 2, 3, 5, 8, 12)
RARITY_DAYS = (7, 10, 14, 21, 30, 45)
ILLEGAL_RARITY_DAYS = (2, 3, 3, 4, 5, 6)

# ---------------------------------------------------------------- 見た目の種類
MACHINE, MAGIC, NEUTRAL = "machine", "magic", "neutral"
THEMES = (MACHINE, MAGIC, NEUTRAL)
#: 設定の値（画面の選択肢）→ 見た目の種類。`AUTO` は LLM の判定に任せる。
AUTO = "自動"
THEME_CHOICES = {"機械": MACHINE, "魔術": MAGIC, "中立": NEUTRAL}
#: 種類ごとの既定の言葉。題と違法の呼び方は、LLM の判定か設定で置き換わる。
THEME_WORDS = {
    MACHINE: {"title": "人体改造", "illegal": "違法", "augment": "改造", "install": "施術を受ける"},
    MAGIC: {"title": "魔導施術", "illegal": "禁術", "augment": "施術", "install": "施術を受ける"},
    NEUTRAL: {"title": "人体改造", "illegal": "違法", "augment": "改造", "install": "施術を受ける"},
}
#: 治療院の品揃えの頼みに足す一文（見た目の種類ごと。舞台の物の名前は書かない）。
SHOP_NOTES = {
    MACHINE: "体に組み込むための部品や素材（義肢・埋め込みの部品など）も、武器や防具ではなく素材か道具として少し並べる。",
    MAGIC: "体に組み込むための素材（施術に使う魔術の素材・生き物の部位など）も、武器や防具ではなく素材か道具として少し並べる。",
    NEUTRAL: "体に組み込むための素材や部品も、武器や防具ではなく素材か道具として少し並べる。",
}
#: 題と違法の呼び方の上限。頼む字数（8字・4字）より余らせる。
#: LLM は少し超えて返すことがあり、頼む字数で切ると語の途中で切れた（「非正規改造」→「非正規改」。VERIFICATION 3.134）。
TITLE_LIMIT = 10
ILLEGAL_WORD_LIMIT = 6
NAME_LIMIT = 12
DESCRIPTION_LIMIT = 60
REASON_LIMIT = 40


class Config(object):
    """設定の値の束（入口が mod.json の設定から作る。検査は既定のまま使う）。"""

    def __init__(self, **values):
        self.fee_base = 200               # 料金の基準（素のゲームの治療費と同じ）
        self.fee_per_value = 20           # 品の値段 1 につき足す料金
        self.illegal_fee_percent = 150    # 違法の施術の料金（合法に対する %）
        self.remove_fee_percent = 50      # 取り外しの料金（施術に対する %）
        self.days_percent = 100           # 入院の日数（表に対する %）
        self.remove_days_percent = 50     # 取り外しの日数（施術に対する %）
        self.fail_percent = 100           # 合法の施術の失敗の見込み（表に対する %）
        self.illegal_fail = 30.0          # 違法の施術の失敗の見込み（%）
        self.overload_fail = 15.0         # 2つめの枠に入れるときに足す失敗の見込み（%）
        self.special_min = 3              # 合法の施術に特殊効果が付くレア度の段（3 = epic）
        for name, value in values.items():
            if hasattr(self, name) and _number(value):
                setattr(self, name, value)


def _number(value):
    return not isinstance(value, bool) and isinstance(value, (int, float))


def rarity_index(rarity):
    """レア度の段（0〜5）。知らない値は common。"""
    return RARITIES.index(rarity) if rarity in RARITIES else 0


def clamp(value, low, high):
    return max(low, min(high, value))


# ---------------------------------------------------------------- 品
def fingerprint(info):
    """品の見分け（見立ての控えの鍵）。名前・説明・種類・レア度・値段から作る。

    持ち物の鍵は持ち主が替わると変わりうるので使わない。同じ名前と説明の品は同じ見立てになる。
    """
    parts = [str(info.get(key) or "") for key in ("name", "description", "item_type", "rarity", "value")]
    return hashlib.sha1("\x1f".join(parts).encode("utf-8")).hexdigest()[:16]


def item_info(data):
    """セーブと同じ形の品の辞書から、見立てに使う項目だけを抜く。体に入れられない種類なら None。"""
    if not isinstance(data, dict) or data.get("item_type") not in ITEM_TYPES:
        return None
    value = data.get("value")
    attributes = data.get("attributes") if isinstance(data.get("attributes"), dict) else {}
    return {"name": str(data.get("name") or ""), "description": str(data.get("description") or ""),
            "item_type": data.get("item_type"),
            "rarity": data.get("rarity") if data.get("rarity") in RARITIES else "common",
            "value": int(value) if _number(value) else 0,
            "detail": str(attributes.get("item_detail") or "")}


# ---------------------------------------------------------------- 見立て
def _short(text, limit):
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[:limit]


def _ability_list(value):
    """`"strength, dexterity"` / `["strength"]` / 日本語の名前を、能力値の鍵の並びにする（重複は落とす）。"""
    if isinstance(value, str):
        pieces = value.replace("、", ",").replace("・", ",").replace("/", ",").split(",")
    elif isinstance(value, (list, tuple)):
        pieces = [str(v) for v in value]
    else:
        return []
    reverse = {name: key for key, name in ABILITY_NAMES.items()}
    found = []
    for piece in pieces:
        piece = piece.strip().lower()
        key = piece if piece in ABILITIES else reverse.get(piece.strip())
        if key and key not in found:
            found.append(key)
    return found


def _truthy(value):
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("true", "yes", "1", "はい", "可")


def normalize_appraisal(raw):
    """LLM の見立てを検算して均す。読めなければ None。

    返す形 `{"usable", "part", "name", "description", "boost", "cost", "drawback", "special", "finish",
    "restricted", "reason", "rule"}`。
    伸ばす能力値は主と副の2つ。答えが1つなら、部位の既定（重なれば表の並びの先頭）を副にする。
    体に適合しない品は `usable` が False で、`reason` に短い理由。
    部位・能力値・特殊効果は表に無い値を表の既定へ落とす（LLM には論理を任せない）。
    """
    if not isinstance(raw, dict):
        return None
    usable = _truthy(raw.get("usable"))
    reason = _short(raw.get("reason"), REASON_LIMIT)
    if not usable:
        return {"usable": False, "reason": reason or "体に適合するアイテムではない", "rule": APPRAISAL_RULE}
    part = str(raw.get("part") or "").strip().lower()
    if part not in PARTS:
        reverse = {name: key for key, name in PART_NAMES.items()}
        part = reverse.get(str(raw.get("part") or "").strip())
    if part not in PARTS:
        return None
    name = _short(raw.get("name"), NAME_LIMIT)
    if not name:
        return None
    boost = _ability_list(raw.get("boost"))[:2] or [PART_BOOST[part]]
    if len(boost) < 2:
        boost.append(next(key for key in (PART_BOOST[part],) + ABILITIES if key not in boost))
    wanted = _ability_list(raw.get("cost"))
    special = str(raw.get("special") or "").strip().lower()
    finish = str(raw.get("finish") or "").strip().lower()
    drawback = str(raw.get("drawback") or "").strip().lower()
    if drawback not in DRAWBACKS:
        reverse = {label: key for key, label in DRAWBACK_NAMES.items()}
        drawback = reverse.get(str(raw.get("drawback") or "").strip(), PART_DRAWBACK[part])
    return {"usable": True, "part": part, "name": name,
            "description": _short(raw.get("description"), DESCRIPTION_LIMIT),
            "boost": boost, "cost": cost_for(part, boost, wanted[0] if wanted else None), "drawback": drawback,
            "special": special if special in PART_SPECIALS[part] else PART_SPECIALS[part][0],
            "finish": finish if finish in FINISHES else PROCESSED, "restricted": _truthy(raw.get("restricted")),
            "reason": "", "rule": APPRAISAL_RULE}


def current_appraisal(found):
    """控えの見立てが今の決まりの版のものならそれ、ほかは None（見立て直す）。"""
    return found if isinstance(found, dict) and found.get("rule") == APPRAISAL_RULE else None


def normalize_batch(raw, count):
    """まとめて頼んだ見立ての答え `{"items": [{"index", ...}]}` を `{品の番号（0 起点）: 見立て}` にする。

    番号は 1 起点で頼む。範囲外・重複・読めない答えは落とす（落ちた品は見立てに失敗した扱い）。
    """
    rows = raw.get("items") if isinstance(raw, dict) else None
    found = {}
    if not isinstance(rows, (list, tuple)):
        return found
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            index = int(str(row.get("index")).strip()) - 1
        except (TypeError, ValueError):
            continue
        if not 0 <= index < count or index in found:
            continue
        appraisal = normalize_appraisal(row)
        if appraisal is not None:
            found[index] = appraisal
    return found


def candidate_note(tally, at_facility, hint=True):
    """候補の欄の下の一行。`tally` は候補に出していない品の数 `{"pending", "refused", "failed", "unknown"}`。"""
    pieces = []
    if tally.get("pending"):
        pieces.append("鑑定中 {}個".format(tally["pending"]))
    if tally.get("refused"):
        pieces.append("適合しない {}個".format(tally["refused"]))
    if tally.get("failed"):
        pieces.append("鑑定に失敗 {}個（開き直すと頼み直す）".format(tally["failed"]))
    if tally.get("unknown"):
        pieces.append("未鑑定 {}個{}".format(tally["unknown"],
                                         "" if at_facility or not hint else "（施設で開くと鑑定する）"))
    return "・".join(pieces)


def candidate_empty(tally, at_facility):
    """適合する品が1つも無いときの文。見立ての途中なら待つ文。"""
    if tally.get("pending"):
        return "鑑定している……"
    if at_facility or tally.get("refused"):
        return "体に適合するアイテムが無い。素材はクラフトで部品に作ってから持ち込む。"
    return "治療院（または裏の事務所）で開くと、アイテムの鑑定を行う。"


def item_size(data):
    """品の辞書（またはゲームの品）の大きさ `(幅, 高さ)` のマス数。読めなければ 1x1。"""
    get = data.get if isinstance(data, dict) else (lambda name: getattr(data, name, None))
    try:
        return max(1, int(get("width_slots") or 1)), max(1, int(get("height_slots") or 1))
    except (TypeError, ValueError):
        return 1, 1


def _grid_cell(pos):
    try:
        return int(pos[0]), int(pos[1])
    except (TypeError, ValueError, IndexError, KeyError):
        return None


def free_spot(placed, need, cols=GRID_COLS, rows=GRID_ROWS):
    """所持品の欄に `need`（幅, 高さ）の品が入る左下のマス `(x, 下から y)`。入らなければ None。

    `placed` は欄に居る品の `[(grid_pos, (幅, 高さ))]`（装備欄の品は入れない）。
    位置が欄の外・読めない・ほかの品と重なる品は、ゲームが置き直す品として先に空きへ詰める。
    詰めきれなければ、もうあふれているので None。
    """
    taken = set()
    loose = []

    def cells(x, y, w, h):
        return [(cx, cy) for cx in range(x, x + w) for cy in range(y, y + h)]

    def fits(x, y, w, h):
        return 0 <= x and 0 <= y and x + w <= cols and y + h <= rows and not taken.intersection(cells(x, y, w, h))

    for pos, size in placed:
        cell = _grid_cell(pos)
        w, h = size
        if cell is not None and fits(cell[0], cell[1], w, h):
            taken.update(cells(cell[0], cell[1], w, h))
        else:
            loose.append(size)

    def first_fit(w, h):
        for y in range(rows - h + 1):
            for x in range(cols - w + 1):
                if fits(x, y, w, h):
                    taken.update(cells(x, y, w, h))
                    return x, y
        return None

    for w, h in sorted(loose, key=lambda s: -s[0] * s[1]):
        if first_fit(w, h) is None:
            return None
    return first_fit(*need)


def chunks(rows, size=BATCH_SIZE):
    """`rows` を `size` 件ずつに分ける。"""
    return [rows[i:i + size] for i in range(0, len(rows), size)]


def cost_for(part, boost, wanted=None):
    """代償の能力値。見立ての答え → 部位の既定 → 残りの先頭（伸びる能力値と重ねない）。"""
    return next((key for key in (wanted, PART_COST[part]) + ABILITIES if key in ABILITIES and key not in boost),
                None)


def cost_range(rarity, illegal):
    """代償の量の幅 `(下限, 上限)`。"""
    table = COST_RANGE_ILLEGAL if illegal else COST_RANGE_LEGAL
    return table[1 if rarity_index(rarity) >= COST_HIGH_FROM else 0]


def roll_cost(rarity, illegal, rng):
    """代償の量を幅の中から1つ決める（施術のとき）。"""
    low, high = cost_range(rarity, illegal)
    return rng.randint(low, high)


def cost_side(appraisal):
    """合法の施術で付く代償の側（`COST_ABILITY` / `COST_BATTLE`）。

    見立ての部位と名前から決める。同じ見立てなら毎回同じ側になり、施術の前の画面に出せる。
    """
    key = "{}|{}".format(appraisal.get("part") or "", appraisal.get("name") or "")
    return COST_BATTLE if int(hashlib.md5(key.encode("utf-8")).hexdigest(), 16) % 2 else COST_ABILITY


def drawback_amount(kind, rarity, illegal):
    """戦闘の代償の量（%）。種類が表に無ければ 0。"""
    table = DRAWBACK_STRENGTH.get(kind)
    if not table:
        return 0
    if not illegal:
        return table[0]
    return table[2 if rarity_index(rarity) >= COST_HIGH_FROM else 1]


def procedure(appraisal, mode, method):
    """施術の扱い `{"illegal", "forbidden", "allowed"}`。

    `mode` は施設（`"legal"` 治療院 / `"illegal"` 裏の事務所 / None 見るだけ）、`method` は裏の事務所で押したボタンの方法。
    違法かどうかはアイテム（禁制か）と方法だけで決まり、場所では決まらない。
    治療院は禁制のアイテムを扱えない（`allowed` が False）。見るだけのときは方法を `method` のとおりに見せる。
    """
    forbidden = bool((appraisal or {}).get("restricted"))
    if forbidden:
        return {"illegal": True, "forbidden": True, "allowed": mode != "legal"}
    overlimit = mode == "illegal" and method == OVERLIMIT
    return {"illegal": overlimit, "forbidden": False, "allowed": True}


def methods_for(appraisal, mode):
    """画面に並べる施術の方法。裏の事務所の正規のアイテムは通常施術と限界施術の2つ、ほかは1つ。"""
    if mode == "illegal" and not (appraisal or {}).get("restricted"):
        return [NORMAL, OVERLIMIT]
    return [NORMAL]


def install_plan(appraisal, info, bucket, mode, method, config, scores=None, gold=None):
    """1つの方法の見積もり（施術の前の画面に出すもの）。

    `{"method", "proc", "illegal", "slot", "fee", "days", "fail", "effects", "costs", "enough"}`。
    `slot` が None なら入らない。`enough` は所持金が足りるか（読めなければ足りる扱い）。
    代償の能力値の量は施術のときに決まるので、`costs` では幅で見せる。
    """
    proc = procedure(appraisal, mode, method)
    illegal = proc["illegal"]
    preview = make_record(appraisal, info, None, illegal, 0, None, None, config.special_min,
                          forbidden=proc["forbidden"])
    named_cost = ABILITY_NAMES.get(appraisal.get("cost"), "\0") + " "
    estimate = [piece for piece in effect_pieces(preview, costs=True) if not piece[0].startswith(named_cost)]
    piece = cost_piece(appraisal, info.get("rarity"), illegal)
    slot = free_slot(bucket, appraisal["part"], illegal)
    fee = install_fee(info, illegal, config)
    return {"method": method, "proc": proc, "illegal": illegal, "slot": slot, "fee": fee,
            "days": install_days(info, illegal, config),
            "fail": fail_percent(info, appraisal.get("finish"), illegal, slot == 1, scores, config),
            "effects": effect_pieces(preview, costs=False),
            "costs": ([piece] if piece else []) + estimate,
            "enough": gold is None or gold >= fee}


def fail_injury(illegal):
    """施術に失敗したときに部位に足す怪我。"""
    return ILLEGAL_FAIL_INJURY if illegal else FAIL_INJURY


def fail_injury_words(amount, current=0):
    """失敗したときの怪我（`怪我 +30`）。今の怪我と合わせて治療で治らない重さに届くなら `・治療不可` を足す。"""
    if (current or 0) + amount >= LOST_FROM:
        return "怪我 +{}・治療不可".format(amount)
    return "怪我 +{}".format(amount)


def fail_note(part):
    """失敗したときのことの1行（`失敗すると腕の怪我で筋力と器用が下がり、アイテムを失う`）。"""
    names = "と".join(ABILITY_NAMES[key] for key in INJURY_ABILITIES.get(part, ()))
    return "失敗すると{}の怪我で{}が下がり、アイテムを失う".format(PART_NAMES.get(part, part), names)


def cost_kinds(appraisal, illegal):
    """付く代償の側の組。違法は両方、合法は `cost_side` の片方。"""
    if illegal:
        return (COST_ABILITY, COST_BATTLE)
    return (cost_side(appraisal),)


def normalize_theme(raw):
    """LLM の世界観の判定を均す。`{"theme", "title", "illegal"}`。読めなければ None。"""
    if not isinstance(raw, dict):
        return None
    theme = str(raw.get("theme") or "").strip().lower()
    if theme not in THEMES:
        theme = THEME_CHOICES.get(str(raw.get("theme") or "").strip())
    if theme not in THEMES:
        return None
    return {"theme": theme, "title": _short(raw.get("title"), TITLE_LIMIT),
            "illegal": _short(raw.get("illegal"), ILLEGAL_WORD_LIMIT)}


def underworld_label(words):
    """裏の事務所のボタンの名前。違法の呼び方に改造の語がもう入っていれば（「禁術改造」）その語だけ。"""
    illegal, augment = words.get("illegal") or "", words.get("augment") or ""
    if augment and augment in illegal:
        return illegal
    return "{}の{}".format(illegal, augment)


def words_for(theme, detected=None, title="", illegal=""):
    """画面の言葉。設定の文字 → 判定の文字 → 種類の既定 の順に採る。"""
    words = dict(THEME_WORDS.get(theme) or THEME_WORDS[NEUTRAL])
    detected = detected if isinstance(detected, dict) else {}
    if detected.get("theme") == theme:
        words["title"] = detected.get("title") or words["title"]
        words["illegal"] = detected.get("illegal") or words["illegal"]
    words["title"] = _short(title, TITLE_LIMIT) or words["title"]
    words["illegal"] = _short(illegal, ILLEGAL_WORD_LIMIT) or words["illegal"]
    return words


# ---------------------------------------------------------------- 量・料金・日数・失敗
def tier_of(rarity, finish, illegal, forbidden=False):
    """量の段。レア度の段に、未加工は -1（違法は下げない）、限界施術は +2、禁制のアイテムは +3。"""
    tier = rarity_index(rarity)
    if forbidden:
        tier += FORBIDDEN_TIER_STEP
    elif illegal:
        tier += OVERLIMIT_TIER_STEP
    elif finish == RAW:
        tier -= 1
    return clamp(tier, 0, len(BOOST_BY_TIER) - 1)


def special_step(rarity, illegal, special_min=3):
    """特殊効果の強さの段（0〜2）。合法は `special_min` の段から付き、違法は必ず付く。付かなければ None。"""
    index = rarity_index(rarity)
    if illegal:
        return ILLEGAL_STEP[index]
    return LEGAL_STEP[index] if index >= int(special_min) else None


def drawback_for(kind, special):
    """代償の種類。特殊効果 `special` と相対するなら、`DRAWBACK_FALLBACK` の順で相対しない種類に替える。"""
    if special not in OPPOSED.get(kind, ()):
        return kind
    return next(other for other in DRAWBACK_FALLBACK if special not in OPPOSED.get(other, ()))


def build_effects(appraisal, rarity, illegal, special_min=3, loss=None, forbidden=False):
    """見立てとレア度から効果を決める。
    `{"abilities": {鍵: 量}, "battle": {種類: %}, "drawback": {種類: %}, "special": {種類: 値}}`。

    伸ばす能力値は主と副の2つで、量は段の表（`BOOST_BY_TIER`。副が 0 の段では副は付かない）。
    禁制のアイテムは違法の改造で、量の段だけが限界施術より1つ上。
    能力値の代償の量は `loss`（施術のときに `roll_cost` で決めたもの）。None なら幅の上限（見積もりの重い側）。
    代償は、合法なら能力値か戦闘の片方、違法なら両方（`cost_kinds`）。違法は暴走か侵食も付く。
    戦闘の代償は、特殊効果と相対しない種類にする（`drawback_for`）。
    """
    part = appraisal.get("part")
    step = special_step(rarity, illegal, special_min)
    extra = None
    illegal = illegal or forbidden
    if illegal:
        extra = RAMPAGE if part in RAMPAGE_PARTS else EROSION
    amounts = BOOST_BY_TIER[tier_of(rarity, appraisal.get("finish"), illegal, forbidden)]
    abilities = {}
    for index, key in enumerate((appraisal.get("boost") or [])[:2]):
        value = amounts[index]
        if index == 0 and extra == EROSION:
            value += EROSION_BOOST[step]
        if value > 0:
            abilities[key] = abilities.get(key, 0) + value
    sides = cost_kinds(appraisal, illegal)
    cost = appraisal.get("cost")
    if loss is None:
        loss = cost_range(rarity, illegal)[1]
    if COST_ABILITY in sides and cost in ABILITIES and cost not in abilities and loss > 0:
        abilities[cost] = -int(loss)
    chosen = None
    if step is not None:
        chosen = appraisal.get("special")
        if chosen not in PART_SPECIALS.get(part, ()):
            chosen = PART_SPECIALS[part][0] if part in PART_SPECIALS else None
    drawback = {}
    if COST_BATTLE in sides:
        kind = appraisal.get("drawback")
        if kind not in DRAWBACKS:
            kind = PART_DRAWBACK.get(part)
        kind = drawback_for(kind, chosen)
        amount = drawback_amount(kind, rarity, illegal)
        if amount > 0:
            drawback[kind] = amount
    battle, special = {}, {}
    if chosen in BATTLE_STRENGTH:
        battle[chosen] = BATTLE_STRENGTH[chosen][step]
    elif chosen in OWN_STRENGTH:
        special[chosen] = OWN_STRENGTH[chosen][step]
    if extra == RAMPAGE:
        battle["critical"] = battle.get("critical", 0) + RAMPAGE_CRITICAL[step]
        special[RAMPAGE] = RAMPAGE_INJURY
    elif extra == EROSION:
        special[EROSION] = EROSION_INJURY[step]
    return {"abilities": abilities, "battle": battle, "drawback": drawback, "special": special}


def install_fee(info, illegal, config):
    """施術の料金。レア度の倍率 × 基準 ＋ 値段 × 1あたり。違法は割増。"""
    fee = config.fee_base * RARITY_FEE[rarity_index(info.get("rarity"))] + config.fee_per_value * max(
        0, int(info.get("value") or 0))
    if illegal:
        fee = fee * config.illegal_fee_percent / 100.0
    return max(0, int(round(fee)))


def install_days(info, illegal, config):
    table = ILLEGAL_RARITY_DAYS if illegal else RARITY_DAYS
    return max(1, int(round(table[rarity_index(info.get("rarity"))] * config.days_percent / 100.0)))


def remove_fee(record, config, at_underworld=False):
    """取り外しの料金。施術の料金の割合。違法の改造を裏の事務所で外すときは違法の料金を基にする。"""
    illegal = bool(record.get("illegal")) and at_underworld
    fee = install_fee(record.get("item_info") or {}, illegal, config)
    return max(0, int(round(fee * config.remove_fee_percent / 100.0)))


def remove_days(record, config, at_underworld=False):
    illegal = bool(record.get("illegal")) and at_underworld
    days = install_days(record.get("item_info") or {}, illegal, config)
    return max(1, int(round(days * config.remove_days_percent / 100.0)))


def fail_percent(info, finish, illegal, overload, scores, config):
    """失敗の見込み（%）。耐久と判断が基準の 11 より高いほど下がる。"""
    if illegal:
        percent = float(config.illegal_fail) + (float(config.overload_fail) if overload else 0.0)
        floor = ILLEGAL_FAIL_FLOOR
    else:
        percent = (FINISH_FAIL.get(finish, FINISH_FAIL[PROCESSED])
                   + RARITY_FAIL[rarity_index(info.get("rarity"))]) * config.fail_percent / 100.0
        floor = FAIL_FLOOR
    scores = scores if isinstance(scores, dict) else {}
    values = [scores.get(key) for key in FAIL_ABILITIES if _number(scores.get(key))]
    if values:
        percent -= (sum(values) / float(len(values)) - BASE_SCORE) * FAIL_PER_POINT
    return round(clamp(percent, floor, FAIL_CAP), 1)


# ---------------------------------------------------------------- 控え
def new_bucket():
    """周回ごとの控え。`augments` は `{部位: [枠0, 枠1]}`（空の枠は None）。

    `recheck` は、暴走で怪我の値だけを書き、ゲームの段階の決め直しをまだ呼んでいない印
    （戦闘の外で呼ぶ。プロセスをまたいで残すため控えに置く）。
    侵食と自己修復の刻みの経過は改造ごと（控えの `wear_days`）。
    """
    return {"augments": {part: [None] * SLOTS for part in PARTS}, "history": [], "recheck": False}


def normalize_bucket(bucket):
    """読んだ控えを均す。`(控え, 直したか)`。

    刻みの経過が周回に1つだった頃の控え（`wear_days` が周回にある。刻みは30日ごと）は、
    その値を各改造へ移し、侵食と自己修復の量を1サイクルの量に直す（`OLD_WEAR_AMOUNTS`）。
    """
    fixed = False
    if not isinstance(bucket, dict):
        return new_bucket(), True
    augments = bucket.get("augments")
    if not isinstance(augments, dict):
        augments, fixed = {}, True
    for part in PARTS:
        slots = augments.get(part)
        if not isinstance(slots, list):
            slots, fixed = [], True
        slots = [slot if isinstance(slot, dict) else None for slot in slots[:SLOTS]]
        slots += [None] * (SLOTS - len(slots))
        if augments.get(part) != slots:
            fixed = True
        augments[part] = slots
    bucket["augments"] = augments
    if not isinstance(bucket.get("history"), list):
        bucket["history"], fixed = [], True
    if "wear_days" in bucket:
        carry = bucket.pop("wear_days")
        carry = int(carry) if _number(carry) else 0
        for _part, _slot, record in records_of(bucket):
            if not _number(record.get("wear_days")):
                record["wear_days"] = max(0, carry) % WEAR_CYCLE_DAYS
            special = record.get("special")
            for kind, table in OLD_WEAR_AMOUNTS.items():
                if isinstance(special, dict) and _number(special.get(kind)) and special[kind] in table:
                    special[kind] = table[special[kind]]
        fixed = True
    if not isinstance(bucket.get("recheck"), bool):
        bucket["recheck"], fixed = bool(bucket.get("recheck")), True
    return bucket, fixed


def order_bucket(bucket):
    return {"augments": {part: bucket["augments"][part] for part in PARTS},
            "history": bucket.get("history") or [], "recheck": bool(bucket.get("recheck"))}


def records_of(bucket):
    """入っている改造の並び `[(部位, 枠, 控え)]`。部位の並び・枠の順。"""
    found = []
    augments = bucket.get("augments") if isinstance(bucket, dict) else None
    if not isinstance(augments, dict):
        return found
    for part in PARTS:
        for slot, record in enumerate(augments.get(part) or []):
            if isinstance(record, dict):
                found.append((part, slot, record))
    return found


def free_slot(bucket, part, illegal):
    """その部位で次に入る枠。合法は枠0が空いているときだけ、違法は枠0が埋まっていれば枠1（過積載）。

    入らなければ None。
    """
    slots = (bucket.get("augments") or {}).get(part) or [None] * SLOTS
    if slots[0] is None:
        return 0
    if illegal and len(slots) > 1 and slots[1] is None:
        return 1
    return None


def has_illegal(bucket):
    return any(record.get("illegal") for _part, _slot, record in records_of(bucket))


def make_record(appraisal, info, item_data, illegal, slot, day, place, special_min=3, rng=None, forbidden=False):
    """入った改造の控え。`rng` を渡すと代償の量を幅の中から決める（渡さなければ上限。見積もり用）。

    禁制のアイテムの改造は `illegal` と `forbidden` の両方が真。
    """
    illegal = illegal or forbidden
    loss = roll_cost(info.get("rarity"), illegal, rng) if rng is not None else None
    effects = build_effects(appraisal, info.get("rarity"), illegal, special_min, loss, forbidden)
    return {"part": appraisal["part"], "slot": int(slot), "name": appraisal["name"],
            "description": appraisal.get("description") or "", "illegal": bool(illegal),
            "forbidden": bool(forbidden),
            "finish": appraisal.get("finish") or PROCESSED,
            "abilities": effects["abilities"], "battle": effects["battle"], "drawback": effects["drawback"],
            "special": effects["special"],
            "item_info": dict(info), "item": item_data if isinstance(item_data, dict) else None,
            "day": day, "place": place or "", "wear_days": 0}


def special_sum(records, kind):
    return sum(value for record in records
               for key, value in (record.get("special") or {}).items() if key == kind and _number(value))


def augmented_parts(records):
    """改造の入っている部位の数。"""
    return len({record.get("part") for record in records if record.get("part") in PARTS})


def ability_totals(records, losses=None):
    """能力値の補正の合計。`losses` は怪我で下がっている量 `{能力値: 量}`（痛覚遮断が戻す元）。"""
    totals = {}
    for record in records:
        for key, value in (record.get("abilities") or {}).items():
            if key in ABILITIES and _number(value):
                totals[key] = totals.get(key, 0) + int(value)
    if special_sum(records, SYNC) and augmented_parts(records) >= len(PARTS):
        for key in ABILITIES:
            totals[key] = totals.get(key, 0) + SYNC_FULL_BONUS
    percent = min(100, special_sum(records, PAINBLOCK))
    if percent and isinstance(losses, dict):
        for key in ABILITIES:
            loss = losses.get(key)
            if _number(loss) and loss > 0:
                totals[key] = totals.get(key, 0) + int(round(loss * percent / 100.0))
    return {key: totals[key] for key in ABILITIES if totals.get(key)}


def battle_totals(records):
    """戦闘の効果と代償の合計（ローダの種類）。同調は、改造のある部位の数だけ軽減に足す。

    代償の上限はローダの窓口が掛ける（隙と反動は 50%）。隙は、この MOD の分だけ `DRAWBACK_CAPS` で先に止める。
    別の部位から付いた軽減と脆さは打ち消し合い、差だけが多い側に残る。
    """
    totals = {}
    for record in records:
        for field in ("battle", "drawback"):
            for kind, value in (record.get(field) or {}).items():
                if _number(value) and value > 0:
                    totals[kind] = totals.get(kind, 0) + value
    sync = special_sum(records, SYNC) * augmented_parts(records)
    if sync:
        totals["reduce"] = totals.get("reduce", 0) + sync
    if totals.get("reduce") and totals.get("fragile"):
        left = totals.pop("reduce") - totals.pop("fragile")
        if left:
            totals["reduce" if left > 0 else "fragile"] = abs(left)
    for kind, cap in DRAWBACK_CAPS.items():
        if totals.get(kind, 0) > cap:
            totals[kind] = cap
    return totals


#: サイクルごとに怪我を動かす効果。同じサイクルの中では、侵食 → 傷が開く（痛覚遮断）→ 自己修復の順に当てる。
WEAR_KINDS = (EROSION, PAINBLOCK, SELFREPAIR)


def wear_amount(record, kind):
    """改造の1サイクル（暴走は1回倒すごと）に怪我を動かす量（無ければ 0）。

    痛覚遮断の値は能力値を戻す割合なので、傷が開く量は `PAINBLOCK_WORSEN`。
    """
    value = (record.get("special") or {}).get(kind)
    if not _number(value) or value <= 0:
        return 0
    return PAINBLOCK_WORSEN if kind == PAINBLOCK else value


def wear_carry(record):
    """改造のサイクルの中で進んだ日数（組み込んだ日から数える。0〜9）。"""
    value = record.get("wear_days")
    return max(0, int(value)) % WEAR_CYCLE_DAYS if _number(value) else 0


def wear_events(records, moved=0, cycles=0):
    """来るサイクル `[(時, 改造の番号, 種類)]`（時の順）と、進めた後の各改造の経過の日数。

    `moved` は進んだ日数で、改造ごとに組み込んだ日から `WEAR_CYCLE_DAYS` 日ごとに1サイクル（時はその日）。
    `cycles` は日数によらず進めるサイクルの数（依頼の終わり。時は `moved` の後に1つずつ）で、経過の日数は変えない。
    サイクルで動く効果の無い改造の経過は進めない。
    """
    moved = max(0, int(moved or 0))
    cycles = max(0, int(cycles or 0))
    events, carries = [], []
    for index, record in enumerate(records):
        carry = wear_carry(record)
        kinds = [kind for kind in WEAR_KINDS if wear_amount(record, kind)]
        if not kinds:
            carries.append(carry)
            continue
        times = list(range(WEAR_CYCLE_DAYS - carry, moved + 1, WEAR_CYCLE_DAYS))
        times += [moved + step for step in range(1, cycles + 1)]
        for time in times:
            events.extend((time, index, kind) for kind in kinds)
        carries.append((carry + moved) % WEAR_CYCLE_DAYS)
    events.sort(key=lambda event: (event[0], WEAR_KINDS.index(event[2]), event[1]))
    return events, carries


def apply_wear(injuries, records, events):
    """サイクル（`wear_events` の並び）を時の順に怪我に当てる。`{部位: 新しい怪我}`（変わった部位だけ）。

    侵食はその部位の怪我を増やす。傷が開くは、負傷（`HURT_FROM` 以上）の部位の怪我を全部位で増やす。
    どちらも `WEAR_CAP` で止め、もう超えている部位は触らない。
    自己修復は、治療で治る怪我（`LOST_FROM` 未満）を全部位で戻す。
    """
    after = {part: injuries.get(part, 0) for part in PARTS}
    for _time, index, kind in events:
        record = records[index]
        amount = wear_amount(record, kind)
        if kind == EROSION:
            part = record.get("part")
            if part in after and after[part] < WEAR_CAP:
                after[part] = min(WEAR_CAP, after[part] + amount)
        elif kind == PAINBLOCK:
            for part in PARTS:
                if HURT_FROM <= after[part] < WEAR_CAP:
                    after[part] = min(WEAR_CAP, after[part] + amount)
        else:
            for part in PARTS:
                if 0 < after[part] < LOST_FROM:
                    after[part] = max(0, after[part] - amount)
    return {part: value for part, value in after.items() if value != injuries.get(part, 0)}


#: 部位の札の見出しの文（{left} は次のサイクルまでの日数、{amount} は1サイクルの量、{quest} は依頼1回の量）。
#: 自己修復だけが効果の色で、ほかは代償の色。
WEAR_MARK_TEXTS = {
    EROSION: "次の侵食まで {left}日（怪我 +{amount}。依頼で +{quest}）",
    PAINBLOCK: "次の悪化まで {left}日（負傷の部位 +{amount}。依頼で +{quest}）",
    SELFREPAIR: "次の自己修復まで {left}日（全部位の怪我 −{amount}。依頼で −{quest}）",
}


def wear_marks(records):
    """部位の札の見出しに出す、怪我が動く時と量。`{部位: [(文, "minus" か "plus")]}`。

    侵食はその部位、傷が開くは負傷の部位、自己修復は全部位の怪我が動く（改造の入っている部位に出す）。
    次のサイクルまでの日数は改造ごと。同じ部位で日数も同じものは量を足し合わせる。暴走は倒すたびに増える。
    合計の効果の欄には出さない（`_total_lines`）。
    """
    rampage, timed = {}, {}
    for record in records:
        part = record.get("part")
        if part not in PARTS:
            continue
        if wear_amount(record, RAMPAGE):
            rampage[part] = rampage.get(part, 0) + wear_amount(record, RAMPAGE)
        left = WEAR_CYCLE_DAYS - wear_carry(record)
        for kind in WEAR_KINDS:
            if wear_amount(record, kind):
                key = (part, kind, left)
                timed[key] = timed.get(key, 0) + wear_amount(record, kind)
    marks = {}
    for part in PARTS:
        if part in rampage:
            marks.setdefault(part, []).append(("敵を倒すたびに怪我 +{}".format(int(rampage[part])), MINUS))
        for kind in WEAR_KINDS:
            text, side = WEAR_MARK_TEXTS[kind], PLUS if kind == SELFREPAIR else MINUS
            for (where, which, left), amount in sorted(timed.items(), key=lambda item: item[0][2]):
                if where == part and which == kind:
                    marks.setdefault(part, []).append(
                        (text.format(left=left, amount=int(amount), quest=int(amount) * QUEST_CYCLES), side))
    return marks


def rampage_parts(records):
    """暴走の入っている部位（倒すたびに怪我が増える）。"""
    return [record.get("part") for record in records
            if (record.get("special") or {}).get(RAMPAGE) and record.get("part") in PARTS]


# ---------------------------------------------------------------- 画面に出す片
PLUS, MINUS, PLAIN = "plus", "minus", "plain"


def signed(value):
    return "+{}".format(value) if value > 0 else "−{}".format(abs(value))


#: 戦闘の効果の中身の文（長い形の括弧の中）。ローダの `battle_effects.text_of` は値の表記だけ（`15%`・`75% 戦闘に1回`）
#: なので、何が起きるかをここで書く。中身はローダの窓口の決まりのとおり（TECH.md の `battle_effects` の節）。
#: 括弧の中にさらに括弧を入れない。
OWN_BATTLE_TEXTS = {
    "critical": "{}%で一撃の強さが1段上がる",
    "lifesteal": "与えたダメージの{}%を回復",
    "pierce": "敵の防御を{}%減らす",
    "venom": "{}%で当てた敵を毒にする。毒は毎ターン最大体力の" + str(battle_effects.VENOM_RATE) + "%を削る",
    "endure": "{}%で倒れる一撃を体力1で耐える。戦闘に1回",
    "thorns": "受けたダメージの{}%を相手に返す",
    "regen": "毎ターン最大体力の{}%を回復",
    "evade": "{}%で敵の一撃をかわす",
    "resist": "{}%で状態異常を防ぐ",
    "desperate": "体力" + str(battle_effects.DESPERATE_AT) + "%以下のとき、受けるダメージ −{}%",
    "reduce": "受けるダメージ −{}%",
}


def battle_text(kind, value, compact=False):
    """戦闘の効果と代償の文。`compact` は札と合計に出す短い形（`反動 5%`）。

    長い形は名前の後ろに中身を括弧で添える（`隙（15%で攻撃が空を切る）`）。
    """
    name = BATTLE_NAMES.get(kind) or DRAWBACK_NAMES.get(kind, kind)
    if compact:
        return "{} {}%".format(name, value)
    if kind == "fragile":
        return "{}（受けるダメージ +{}%）".format(name, value)
    if kind == "falter":
        return "{}（{}%で攻撃が空を切る）".format(name, value)
    if kind == "unmend":
        return "{}（受ける回復 −{}%）".format(name, value)
    if kind == "backlash":
        return "{}（戦闘の後に最大体力の{}%を失う）".format(name, value)
    if kind in OWN_BATTLE_TEXTS:
        return "{}（{}）".format(name, OWN_BATTLE_TEXTS[kind].format(value))
    # 表に無い種類はローダの窓口の値の表記。負の符号はこの MOD の表記（−）に揃える
    text = battle_effects.text_of(kind, value, None, "ja").replace(" -", " −")
    return "{}（{}）".format(name, text)


def special_text(kind, value, part=None, compact=False):
    """この MOD だけの特殊効果の文と、効果か負担か。`(文, PLUS / MINUS)`。長い形は中身を括弧で添える。"""
    name = SPECIAL_NAMES.get(kind, kind)
    where = PART_NAMES.get(part, "その部位")
    if kind == PAINBLOCK:
        return ("{} {}%".format(name, value) if compact
                else "{}（怪我で下がった能力値を{}%戻す）".format(name, value)), PLUS
    if kind == SYNC:
        return ("{} {}%".format(name, value) if compact
                else "{}（改造のある部位1つにつき軽減 {}%。6部位で全能力 +{}）".format(name, value, SYNC_FULL_BONUS)), PLUS
    if kind == SELFREPAIR:
        return ("{} {}".format(name, value) if compact
                else "{}（{}日ごとに全部位の怪我 −{}。依頼で −{}）".format(
                    name, WEAR_CYCLE_DAYS, value, value * QUEST_CYCLES)), PLUS
    if kind == RAMPAGE:
        return (name if compact else "{}（敵を倒すたびに{}の怪我 +{}）".format(name, where, value)), MINUS
    if kind == EROSION:
        return ("{} {}".format(name, value) if compact
                else "{}（{}日ごとに{}の怪我 +{}。依頼で +{}）".format(
                    name, WEAR_CYCLE_DAYS, where, value, value * QUEST_CYCLES)), MINUS
    return "{} {}".format(name, value), PLUS


#: 痛覚遮断に付く負担の名前（痛みを感じないので傷が開く）。
WORSEN_NAME = "傷が開く"


def worsen_text(compact=False):
    """痛覚遮断に付く負担の文。`(文, MINUS)`。"""
    if compact:
        return WORSEN_NAME, MINUS
    return "{}（{}日ごとに負傷の部位の怪我 +{}。依頼で +{}）".format(
        WORSEN_NAME, WEAR_CYCLE_DAYS, PAINBLOCK_WORSEN, PAINBLOCK_WORSEN * QUEST_CYCLES), MINUS


def cost_piece(appraisal, rarity, illegal):
    """施術の前に見せる能力値の代償の片（量は幅で `器用 0〜−1`）。下がらない幅・付かない側なら None。"""
    key = appraisal.get("cost")
    low, high = cost_range(rarity, illegal)
    if key not in ABILITIES or high <= 0 or COST_ABILITY not in cost_kinds(appraisal, illegal):
        return None
    amount = "−{}".format(high) if low == high else "{}〜−{}".format(
        "0" if low == 0 else "−{}".format(low), high)
    return "{} {}".format(ABILITY_NAMES[key], amount), MINUS


def effect_pieces(record, costs=None, compact=False):
    """控えの効果を `[(文, PLUS / MINUS)]` にする。`costs` が True なら代償だけ、False なら効果だけ、None なら全部。

    `compact` は札に出す短い形（戦闘の効果を `名前 値%` にする）。
    """
    out = []
    for key in ABILITIES:
        value = (record.get("abilities") or {}).get(key)
        if not _number(value) or not value:
            continue
        kind = PLUS if value > 0 else MINUS
        if costs is None or costs == (kind == MINUS):
            out.append(("{} {}".format(ABILITY_NAMES[key], signed(int(value))), kind))
    for kind, value in (record.get("battle") or {}).items():
        if not costs:
            out.append((battle_text(kind, value, compact), PLUS))
    for kind, value in (record.get("drawback") or {}).items():
        if costs is None or costs:
            out.append((battle_text(kind, value, compact), MINUS))
    for kind, value in (record.get("special") or {}).items():
        pieces = [special_text(kind, value, record.get("part"), compact)]
        if kind == PAINBLOCK and wear_amount(record, PAINBLOCK):
            pieces.append(worsen_text(compact))
        for text, side in pieces:
            if costs is None or costs == (side == MINUS):
                out.append((text, side))
    # 伸びるほうを先に、代償を後に（並べ替えても同じ側の中の順は保つ）
    return sorted(out, key=lambda piece: piece[1] == MINUS)


def _total_lines(records):
    """合計の効果のほかの行 `[(短い文, 色, 長い文)]`。長い文はマウスを乗せたときの説明に出す。

    暴走・侵食・傷が開くは部位ごとの負担なので、ここには出さず部位の札の見出しに出す（`wear_marks`）。
    """
    battle = battle_totals(records)
    lines = [(battle_text(kind, value, True), PLUS, battle_text(kind, value)) for kind, value in sorted(battle.items())
             if kind not in DRAWBACKS]
    lines += [(battle_text(kind, battle[kind], True), MINUS, battle_text(kind, battle[kind]))
              for kind in DRAWBACKS if battle.get(kind)]
    for kind in (PAINBLOCK, SELFREPAIR):
        value = special_sum(records, kind)
        if value:
            value = min(100, value) if kind == PAINBLOCK else value
            lines.append((special_text(kind, value, compact=True)[0], PLUS, special_text(kind, value)[0]))
    return lines


def total_pieces(records, losses=None):
    """合計の効果の片。能力値は `(名前, 符号つきの量, 色)`、ほかは `(文, 色)`。"""
    totals = ability_totals(records, losses)
    stats = [(ABILITY_NAMES[key], signed(value), PLUS if value > 0 else MINUS)
             for key, value in totals.items()]
    return stats, [(text, kind) for text, kind, _long in _total_lines(records)]


def total_hints(records):
    """合計の効果のほかの行の長い文（`total_pieces` の行と同じ並び）。マウスを乗せたときの説明に出す。"""
    return [long for _text, _kind, long in _total_lines(records)]


def days_words(days):
    """ゲームの書き方の日数（30日の倍数は「Nヵ月」）。"""
    days = int(days)
    if days >= 30 and days % 30 == 0:
        return "{}ヵ月".format(days // 30)
    return "{}日".format(days)


def success_percent(fail):
    """失敗の見込み（%）から成功率（%）。小数の第1位まで。"""
    return round(100.0 - float(fail), 1)


def percent_words(value):
    value = round(float(value), 1)
    return "{:g}%".format(value)
