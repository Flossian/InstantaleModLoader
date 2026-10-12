# -*- coding: utf-8 -*-
"""街の出来事の規則。ゲームには触らない（ゲーム抜きの検査で全部通せる）。

表・起こり方・段の進み方・値段の率・控えの形を持つ。
遊び方は DOC.md、決めた仕様と控えの形（他の MOD との取り決め）は VERIFICATION.md §3.97。
"""

BUY = "買価"
SELL = "売価"
#: 値段の効き目の品の区分。ゲームの `item_type` と `item_detail` から決める（`category_of`）。
#: 食べ物（food・drink）はゲームでは大半が healing_item に入っているので、`item_type` だけでは分けない
#: （GAME.md の品の分類の節）。
CATEGORIES = ("weapon", "wearable", "medicine", "food", "utility", "material")
FOOD_DETAILS = ("food", "drink")
STAGES = (1, 2, 3)
STAGE_NAMES = {1: "兆し", 2: "悪化", 3: "深刻"}
END_PLAYER = "player"
END_RIVAL = "rival"
END_NATURAL = "natural"
PENDING = "pending"
ACTIVE = "active"
ENDED = "ended"
HISTORY_LIMIT = 20
BUCKET_KEYS = ("events", "seq", "next_roll_day", "cooldown", "cleanup")
EVENT_KEYS = ("id", "area", "area_name", "kind", "stage", "status", "started", "stage_day",
              "ended", "end_by", "quest", "goods", "tries", "text", "told")
TEXT_KEYS = ("name", "summary", "arrive_1", "arrive_2", "arrive_3", "shop", "settled", "quest", "rumor")

ALL = None      # 効き目の品の種類で「全品」
GOODS = "goods"  # 効き目の品の種類で「発生のときに選んだ1種」

# 1つの出来事:
#   key       控えに書く英名（世界観に依らない。取り決め）
#   name      抽象の呼び名（LLM が書けなかったとき・窓の既定）
#   what      LLM に渡す説明（何が起きたか）
#   examples  世界ごとの例（LLM に渡す。中世／西部劇／サイバーパンク／ポストアポカリプスの順）
#   effects   値段の効き目 [(品の種類の並び or ALL or GOODS, "buy"/"sell"/"both", (1段, 2段, 3段) の %)]
#   inn       宿代の効き目（1段, 2段, 3段）の % か None
#   size      起こりやすさが 3 になる規模（village/town/city）。"any" はどの規模でも 2
#   arrive    街に着いたときの1行（段ごと。LLM が書けなかったときの文）
#   shop / settled / quest / rumor   同上
KINDS = (
    {"key": "outlaws", "name": "無法者の横行",
     "what": "武装した無法者の一団が街の周りで荷や旅人を襲っている",
     "examples": "盗賊団／無法者の一味／ギャング／略奪者",
     "effects": [(ALL, "buy", (10, 20, 35))], "inn": None, "size": ("town",),
     "arrive": ("街道で荷が襲われたという話が、街のあちこちで囁かれている。",
                "武装した無法者の一団が街の周りを荒らし、人々は日暮れ前に戸を閉ざしている。",
                "無法者が街の外を我が物顔で行き来し、荷はほとんど届かなくなっている。"),
     "shop": "無法者に荷を奪われ、品の値が上がっている。",
     "settled": "街を荒らしていた無法者の一団は、どこかへ流れていったらしい。",
     "quest": "この街の周りで荷や旅人を襲う無法者の一団を討伐する依頼",
     "rumor": "{town}の周りで、無法者の一団が荷を襲っているらしい。"},
    {"key": "epidemic", "name": "感染症の流行",
     "what": "街で病が広がり、人々が倒れている",
     "examples": "疫病／コレラ／生体ウイルス／変異病",
     "effects": [(("medicine",), "buy", (25, 50, 100)), (("food",), "buy", (10, 20, 30))],
     "inn": None, "size": ("city",),
     "arrive": ("咳き込む人の姿が、街のあちこちで目に付く。",
                "病に倒れる者が相次ぎ、通りには人影がまばらになっている。",
                "病が街じゅうに広がり、閉ざされた家の戸口には印が付けられている。"),
     "shop": "病が流行り、薬の類が飛ぶように売れて値が上がっている。",
     "settled": "街に広がっていた病は、ようやく下火になったらしい。",
     "quest": "この街に広がる病の源を突き止めて断つ依頼",
     "rumor": "{town}で病が流行り、倒れる者が相次いでいるらしい。"},
    {"key": "invaders", "name": "外敵の襲来",
     "what": "人ではない脅威の群れが街の周りに現れ、人や家畜を襲っている",
     "examples": "魔物の群れ／狼やコヨーテの群れ／暴走した戦闘ドローン／変異生物の群れ",
     "effects": [(("weapon", "wearable"), "both", (15, 30, 50))], "inn": None, "size": ("village",),
     "arrive": ("街の外で、得体の知れない群れを見たという者がいる。",
                "群れが街の外れを襲い、人々は武器を手放さなくなっている。",
                "群れが街を取り巻き、門の外へ出る者はほとんどいない。"),
     "shop": "群れに備えて武具を求める者が多く、武具の値が上がっている。",
     "settled": "街を脅かしていた群れは、どこかへ去っていったらしい。",
     "quest": "この街を襲う群れの長か、群れの湧き出る元を断つ依頼",
     "rumor": "{town}の周りに、人を襲う群れが出ているらしい。"},
    {"key": "unrest", "name": "騒乱",
     "what": "住民の不満が爆発し、街で騒ぎや衝突が起きている",
     "examples": "一揆／鉱夫のストライキ／暴動／配給を巡る騒ぎ",
     "effects": [(ALL, "sell", (-10, -20, -35)), (ALL, "buy", (5, 10, 15))], "inn": None,
     "size": ("city",),
     "arrive": ("広場で声を荒げる人々の姿がある。",
                "街のあちこちで住民と取り締まる側がぶつかり合っている。",
                "騒ぎが街じゅうに広がり、打ち壊された店先が目立つ。"),
     "shop": "騒ぎで先行きが見えず、店は品の買い取りを渋っている。",
     "settled": "街を揺るがした騒ぎは、ひとまず鎮まったらしい。",
     "quest": "この街の騒ぎを煽る者を捕らえるか、争いの元を収める依頼",
     "rumor": "{town}で住民の騒ぎが起きているらしい。"},
    {"key": "shortage", "name": "物資の途絶",
     "what": "街を支える物資の供給が断たれ、食べ物や日々の物が足りなくなっている",
     "examples": "凶作／旱魃／工場の停止・物流の断絶／水源の汚染",
     "effects": [(("food",), "buy", (20, 40, 80))], "inn": (20, 40, 80),
     "size": ("village",),
     "arrive": ("店先の品が、いつもより目に見えて少ない。",
                "食べ物や日々の物が足りず、人々は分け合って凌いでいる。",
                "物資が尽きかけ、街には飢えと苛立ちが広がっている。"),
     "shop": "物資が届かず、食べ物の類の値が上がっている。",
     "settled": "途絶えていた物資は、再び街へ届くようになったらしい。",
     "quest": "この街への物資を断っている原因を突き止めて取り除く依頼",
     "rumor": "{town}では物資が足りず、人々が困っているらしい。"},
    {"key": "aftermath", "name": "災害の後",
     "what": "街を大きな災害が襲い、建物や設備が壊れたまま復旧が進んでいない",
     "examples": "大火／竜巻／大規模な停電・設備の崩落／砂嵐・地震",
     "effects": [(("material", "utility"), "both", (15, 30, 50))], "inn": None, "size": "any",
     "arrive": ("災害の爪痕が、街のあちこちに残っている。",
                "崩れたままの建物が多く、復旧の手が足りていない。",
                "街の一角はいまも瓦礫に埋もれ、人々は仮の住まいで暮らしている。"),
     "shop": "復旧に使う資材や道具が足りず、値が上がっている。",
     "settled": "災害で傷んだ街は、少しずつ元の姿を取り戻しつつあるらしい。",
     "quest": "この街の復旧に要るものを集めるか、災害の元を始末する依頼",
     "rumor": "{town}が災害に見舞われ、復旧が進んでいないらしい。"},
    {"key": "counterfeit", "name": "通貨の信用の揺らぎ",
     "what": "偽の通貨や支払いの手段が出回り、店が客の支払いを疑っている",
     "examples": "偽金／偽札／決済の改竄・クレジットの偽造／配給札の偽造",
     "effects": [(ALL, "sell", (-10, -20, -35))], "inn": None, "size": ("city",),
     "arrive": ("偽の金が出回っているという噂がある。",
                "店主たちは客の支払いを一つ一つ疑ってかかっている。",
                "偽の金が街にあふれ、まともな取引が成り立たなくなりつつある。"),
     "shop": "偽の金が出回っていて、店は買い取りの額を渋っている。",
     "settled": "出回っていた偽の金は、もう見かけなくなったらしい。",
     "quest": "この街に偽の通貨を流している元締めを摘発する依頼",
     "rumor": "{town}で偽の金が出回っているらしい。"},
    {"key": "hoarding", "name": "買い占め",
     "what": "一部の者が品を買い占めて値を吊り上げている",
     "examples": "商人の結託／鉄道会社の独占／企業の独占／水を握った一派",
     "effects": [(GOODS, "buy", (30, 60, 120))], "inn": None, "size": ("town",),
     "arrive": ("ある品だけが、どの店でも妙に高い。",
                "買い占めた者が値を吊り上げ、人々は手が出せずにいる。",
                "買い占めは街じゅうの店に及び、人々の怒りが募っている。"),
     "shop": "{goods}が買い占められ、値が吊り上がっている。",
     "settled": "品の買い占めは崩れ、値は落ち着いたらしい。",
     "quest": "この街で品を買い占めて値を吊り上げている黒幕を摘発する依頼",
     "rumor": "{town}で品が買い占められ、値が吊り上がっているらしい。"},
    {"key": "anomaly", "name": "不可解な怪事",
     "what": "説明のつかない出来事が街で続き、人々が怯えている",
     "examples": "怪異／幽霊話／謎の信号・電脳の幽霊／異常地帯の拡大",
     "effects": [(("utility",), "buy", (15, 30, 50))], "inn": (10, 25, 50), "size": "any",
     "arrive": ("夜な夜な不可解なことが起きると、街の人々が声を潜めて話している。",
                "説明のつかない出来事が続き、日が落ちると誰も外へ出ようとしない。",
                "怪事は昼にまで及び、街を捨てて出ていく者も出始めている。"),
     "shop": "怪事を恐れて身を守る道具を求める者が多く、値が上がっている。",
     "settled": "街を騒がせていた怪事は、ぱったりと止んだらしい。",
     "quest": "この街で続く説明のつかない出来事の源を突き止める依頼",
     "rumor": "{town}で説明のつかない出来事が続いているらしい。"},
    {"key": "abductions", "name": "失踪の続発",
     "what": "街の人々が次々と姿を消している",
     "examples": "人さらい／人狩り／臓器狩り／奴隷狩り",
     "effects": [], "inn": (20, 40, 80), "size": ("village",),
     "arrive": ("姿を消した者がいると、街の人々が不安げに話している。",
                "失踪が相次ぎ、人々は一人で出歩かなくなっている。",
                "失踪は止まず、宿も用心棒を雇わねば客を泊められなくなっている。"),
     "shop": "",
     "settled": "相次いでいた失踪は、ようやく止まったらしい。",
     "quest": "この街で人々をさらっている一味の根城を突き止める依頼",
     "rumor": "{town}で人が次々と姿を消しているらしい。"},
    {"key": "contraband", "name": "闇物資の氾濫",
     "what": "出所の怪しい品が街に大量に出回っている",
     "examples": "密輸品／密造酒／違法な改造部品／略奪品の横流し",
     "effects": [(ALL, "both", (-10, -15, -25))], "inn": None, "size": ("city",),
     "arrive": ("出所の怪しい品が、裏通りで安く売られている。",
                "闇の品が表の店にまで並び、値が崩れている。",
                "闇の品が街にあふれ、まともな商いが立ち行かなくなっている。"),
     "shop": "出所の怪しい安物が出回り、品の値が崩れている。",
     "settled": "出回っていた闇の品は、締め付けで消えたらしい。",
     "quest": "この街に出所の怪しい品を流している一味を摘発する依頼",
     "rumor": "{town}に出所の怪しい品が出回っているらしい。"},
    {"key": "crackdown", "name": "締め付けの強化",
     "what": "街を治める側が取り締まりを強め、人々の出入りや持ち物を厳しく調べている",
     "examples": "衛兵の検問／保安官の締め付け／企業警備の戒厳／軍閥の統制",
     "effects": [(("weapon", "wearable"), "buy", (20, 40, 60)), (ALL, "sell", (-5, -10, -15))],
     "inn": None, "size": ("city",),
     "arrive": ("街の入口で、持ち物を調べられている者がいる。",
                "取り締まりが厳しくなり、人々は顔を伏せて足早に通り過ぎる。",
                "街は締め付けで息苦しく、些細なことで連れて行かれる者もいる。"),
     "shop": "取り締まりで武具の持ち込みが絞られ、値が上がっている。",
     "settled": "厳しかった取り締まりは、ようやく緩んだらしい。",
     "quest": "この街の締め付けを敷いた者の不正を暴くか、締め付けの元になった脅威を除く依頼",
     "rumor": "{town}では取り締まりが厳しくなっているらしい。"},
    {"key": "power_vacuum", "name": "権力の空白",
     "what": "街を治めていた者が突然いなくなり、後釜を巡って争いが起きている",
     "examples": "領主の急死／保安官の殺害／企業幹部の暗殺／首領の死",
     "effects": [(ALL, "buy", (5, 10, 20)), (ALL, "sell", (-5, -10, -20))], "inn": None,
     "size": ("city",),
     "arrive": ("街を治めていた者がいなくなったという話で持ちきりだ。",
                "後釜を巡る争いが表に出て、街の空気が張り詰めている。",
                "後釜争いが血を見るまでになり、街は二つに割れている。"),
     "shop": "先行きが読めず、店は値を守りに入っている。",
     "settled": "街を治める次の者が決まり、争いは収まったらしい。",
     "quest": "この街の後釜争いを収める依頼（後継の護衛か、刺客の捕縛など）",
     "rumor": "{town}では治めていた者がいなくなり、後釜争いが起きているらしい。"},
    {"key": "influx", "name": "人の流入",
     "what": "よそから大勢の人が流れ込み、街が受け止めきれずにいる",
     "examples": "難民／開拓民／移民／避難民",
     "effects": [(("food",), "buy", (10, 25, 50))], "inn": (25, 50, 100), "size": ("town",),
     "arrive": ("見慣れない顔の旅人が、街にいつもより多い。",
                "よそから流れ込んだ人々で、宿も通りもあふれている。",
                "流れ込んだ人々と住民の間で、いさかいが絶えなくなっている。"),
     "shop": "人があふれて食べ物の類が足りず、値が上がっている。",
     "settled": "流れ込んだ人々は、それぞれの落ち着き先を見つけたらしい。",
     "quest": "この街で流れ込んだ人々と住民のもめ事を収めるか、人々を追い立てた元を断つ依頼",
     "rumor": "{town}によそから大勢の人が流れ込んでいるらしい。"},
    {"key": "boom", "name": "にわか景気",
     "what": "思わぬ富の種が見つかり、一攫千金を狙う者が街に押し寄せている",
     "examples": "新しい鉱脈／ゴールドラッシュ／技術の流行／遺物の発見",
     "effects": [(ALL, "buy", (10, 20, 30)), (ALL, "sell", (10, 25, 40))], "inn": None,
     "size": ("town",),
     "arrive": ("一儲けの噂を聞きつけた者が、街に集まり始めている。",
                "一攫千金を狙う者が押し寄せ、街は浮き足立っている。",
                "景気に群がった荒くれ者が街を我が物顔で歩き、喧嘩が絶えない。"),
     "shop": "景気に沸いて、品の値も買い取りの額も上がっている。",
     "settled": "街を沸かせたにわか景気は、熱が冷めて落ち着いたらしい。",
     "quest": "この街のにわか景気に群がった荒くれ者を鎮める依頼",
     "rumor": "{town}がにわか景気に沸いているらしい。"},
)
KIND_BY_KEY = {kind["key"]: kind for kind in KINDS}
GOODS_NAMES = {"weapon": "武器", "wearable": "防具", "medicine": "薬の類",
               "food": "食べ物の類", "utility": "道具", "material": "素材"}


def category_of(item_type, item_detail=None):
    """値段の効き目の区分。ゲームの分類から決める。読めなければ None。"""
    if item_type in ("weapon", "wearable", "utility", "material"):
        return item_type
    if item_type in ("healing_item", "consumable"):
        if item_detail in FOOD_DETAILS:
            return "food"
        return "medicine" if item_type == "healing_item" else "utility"
    return None


# ------------------------------------------------------------ 控え
def new_bucket():
    return {"events": [], "seq": 0, "next_roll_day": None, "cooldown": {}, "cleanup": []}


def _int(value, default=None):
    return value if isinstance(value, int) and not isinstance(value, bool) else default


def order_bucket(bucket):
    """書く直前に並びを固定する（セーブの項目の並びと同じく、読み手の表示を崩さないため）。"""
    fresh = new_bucket()
    out = {}
    for key in BUCKET_KEYS:
        value = bucket.get(key, fresh[key]) if isinstance(bucket, dict) else fresh[key]
        if key == "events":
            value = [order_event(event) for event in value if isinstance(event, dict)] \
                if isinstance(value, list) else []
        elif key == "cooldown":
            value = {str(area): _int(day, 0) for area, day in sorted(
                (value if isinstance(value, dict) else {}).items(), key=lambda item: _id_key(item[0]))}
        elif key == "cleanup":
            value = [str(quest) for quest in value] if isinstance(value, list) else []
        out[key] = value
    return out


def order_event(event):
    out = {key: event.get(key) for key in EVENT_KEYS}
    text = event.get("text") if isinstance(event.get("text"), dict) else {}
    out["text"] = {key: text.get(key) for key in TEXT_KEYS if text.get(key)}
    out["told"] = dict(event.get("told") or {}) if isinstance(event.get("told"), dict) else {}
    return out


def _id_key(value):
    text = str(value)
    return (0, int(text)) if text.isdigit() else (1, text)


def events_of(bucket, status=None):
    events = bucket.get("events") if isinstance(bucket, dict) else None
    events = events if isinstance(events, list) else []
    return [event for event in events if isinstance(event, dict)
            and (status is None or event.get("status") == status)]


def live_in(bucket, area_id):
    """その街で効き目を出している出来事（1つまで）。無ければ None。"""
    for event in events_of(bucket, ACTIVE):
        if str(event.get("area")) == str(area_id):
            return event
    return None


def open_in(bucket, area_id):
    """その街でまだ終わっていない出来事（答え待ちも入る）。"""
    for event in events_of(bucket):
        if event.get("status") in (PENDING, ACTIVE) and str(event.get("area")) == str(area_id):
            return event
    return None


def event_by_id(bucket, event_id):
    for event in events_of(bucket):
        if event.get("id") == event_id:
            return event
    return None


# ------------------------------------------------------------ 起こり方
def weights(kinds, size, by_size=True):
    """`[(kind, 重み)]`。規模で起こりやすさを変えないなら全部 1。"""
    out = []
    for kind in kinds:
        if not by_size:
            out.append((kind, 1))
        elif kind["size"] == "any":
            out.append((kind, 2))
        elif size in kind["size"]:
            out.append((kind, 3))
        else:
            out.append((kind, 1))
    return out


def pick_kind(rng, kinds, size, by_size=True, exclude=()):
    rows = [(kind, weight) for kind, weight in weights(kinds, size, by_size)
            if kind["key"] not in exclude]
    total = sum(weight for _kind, weight in rows)
    if total <= 0:
        return None
    roll = rng.random() * total
    for kind, weight in rows:
        roll -= weight
        if roll < 0:
            return kind
    return rows[-1][0]


def may_roll(bucket, day):
    """抽選の日に届いているか。初回は届いた日から数え始める（その回は引かない）。"""
    if day is None:
        return False
    next_day = _int(bucket.get("next_roll_day"))
    return next_day is not None and day >= next_day


def roll(bucket, day, towns, rng, kinds, *, chance_percent, interval_days, max_active,
         by_size=True):
    """抽選を1回する。起きたら新しい出来事（答え待ち）を台帳に入れて返す。起きなければ None。

    `towns` は候補の街 `[(土地 id, 名前, 規模)]`（訪れたことがあり、ギルドのある街）。
    抽選の日を数えるのはここ。1回の日数送りで何日経っても、引くのは1回だけ。
    """
    if day is None:
        return None
    if _int(bucket.get("next_roll_day")) is None:
        bucket["next_roll_day"] = day + max(1, int(interval_days))
        return None
    if not may_roll(bucket, day):
        return None
    bucket["next_roll_day"] = day + max(1, int(interval_days))
    if not kinds:
        return None
    if len([event for event in events_of(bucket) if event.get("status") in (PENDING, ACTIVE)]) \
            >= max(0, int(max_active)):
        return None
    if rng.random() >= max(0, int(chance_percent)) / 100.0:
        return None
    cooldown = bucket.get("cooldown") if isinstance(bucket.get("cooldown"), dict) else {}
    free = [(area, name, size) for area, name, size in towns
            if open_in(bucket, area) is None and day >= _int(cooldown.get(str(area)), -1)]
    if not free:
        return None
    area, name, size = free[int(rng.random() * len(free)) % len(free)]
    kind = pick_kind(rng, kinds, size, by_size)
    if kind is None:
        return None
    return start(bucket, day, area, name, kind, rng)


def start(bucket, day, area, name, kind, rng):
    bucket["seq"] = _int(bucket.get("seq"), 0) + 1
    event = {"id": bucket["seq"], "area": str(area), "area_name": name or "", "kind": kind["key"],
             "stage": 1, "status": PENDING, "started": day, "stage_day": day, "ended": None,
             "end_by": None, "quest": None, "goods": None, "tries": [kind["key"]], "text": {},
             "told": {}}
    if any(target == GOODS for target, _side, _rates in kind["effects"]):
        event["goods"] = CATEGORIES[int(rng.random() * len(CATEGORIES)) % len(CATEGORIES)]
    bucket.setdefault("events", []).append(event)
    return event


def change_kind(event, kind, rng):
    """世界に合わないと言われた出来事を、別の種類にする。"""
    event["kind"] = kind["key"]
    event["tries"] = list(event.get("tries") or []) + [kind["key"]]
    event["goods"] = None
    if any(target == GOODS for target, _side, _rates in kind["effects"]):
        event["goods"] = CATEGORIES[int(rng.random() * len(CATEGORIES)) % len(CATEGORIES)]


def drop(bucket, event):
    bucket["events"] = [other for other in events_of(bucket) if other is not event]


# ------------------------------------------------------------ 段の進み方
def advance(bucket, day, *, stage_days, settle_days, steps_per_elapse, rest_days=0):
    """日数が進んだ後に段を進める。`[(出来事, "stage"|"settled", その時の段)]` を返す。

    1回の日数送りで1つの出来事が進むのは `steps_per_elapse` 段まで（自然に収まるのも1段に数える）。
    段の起点（`stage_day`）は、1回の中で続けて上がる間は日数を積み上げ、上限で打ち切られたら
    その日に置き直す（打ち切った後の段は、また丸ごと間隔ぶん続く）。
    """
    changes = []
    if day is None:
        return changes
    cap = max(1, int(steps_per_elapse))
    for event in events_of(bucket, ACTIVE):
        steps = 0
        while True:
            since = day - _int(event.get("stage_day"), day)
            stage = _int(event.get("stage"), 1)
            due_stage = stage < STAGES[-1] and since >= max(1, int(stage_days))
            due_end = stage >= STAGES[-1] and since >= max(1, int(settle_days))
            if not (due_stage or due_end):
                break
            if steps >= cap:
                event["stage_day"] = day
                break
            steps += 1
            if due_end:
                end(bucket, event, day, END_NATURAL, rest_days)
                changes.append((event, "settled", stage))
                break
            event["stage"] = stage + 1
            event["stage_day"] = _int(event.get("stage_day"), day) + max(1, int(stage_days))
            changes.append((event, "stage", stage + 1))
    return changes


def end(bucket, event, day, by, rest_days=None):
    event["status"] = ENDED
    event["ended"] = day
    event["end_by"] = by
    if rest_days is not None and day is not None:
        bucket.setdefault("cooldown", {})[str(event.get("area"))] = day + max(0, int(rest_days))
    trim(bucket)


def trim(bucket, limit=HISTORY_LIMIT):
    """終わった出来事は直近の `limit` 件だけ残す（`339_` が「前回から」を読むため。VERIFICATION.md §3.97「控えの形」）。"""
    events = events_of(bucket)
    ended = [event for event in events if event.get("status") == ENDED]
    if len(ended) <= limit:
        return
    keep = set(id(event) for event in sorted(
        ended, key=lambda event: _int(event.get("ended"), 0))[-limit:])
    bucket["events"] = [event for event in events
                        if event.get("status") != ENDED or id(event) in keep]


def days_to_next(event, day, stage_days, settle_days):
    """次に段が変わるまでの日数（3段なら収まるまで）。読めなければ None。"""
    if day is None or event.get("status") != ACTIVE:
        return None
    since = day - _int(event.get("stage_day"), day)
    limit = settle_days if _int(event.get("stage"), 1) >= STAGES[-1] else stage_days
    return max(0, int(limit) - since)


def rewind(bucket, day):
    """古いセーブを読んだ（暦が控えより前）。その日より後に起きたことを控えから外す。直したら True。

    その日より後に始まった出来事は消す。その日より後に終わった出来事は進行中へ戻す（段はそのまま）。
    """
    if day is None:
        return False
    changed = False
    keep = []
    for event in events_of(bucket):
        started = _int(event.get("started"), 0)
        if started > day:
            changed = True
            continue
        ended = _int(event.get("ended"))
        if ended is not None and ended > day:
            event["status"], event["ended"], event["end_by"] = ACTIVE, None, None
            changed = True
        if _int(event.get("stage_day"), 0) > day:
            event["stage_day"] = day
            changed = True
        keep.append(event)
    bucket["events"] = keep
    next_day = _int(bucket.get("next_roll_day"))
    if next_day is not None and next_day > day + 3650:
        bucket["next_roll_day"] = None
        changed = True
    return changed


# ------------------------------------------------------------ 効き目
def _targets(target, event):
    if target is ALL:
        return None
    if target == GOODS:
        return (event.get("goods"),) if event.get("goods") else ()
    return tuple(target)


def price_rate(event, category, key, strength_percent=100):
    """その区分（`category_of`）の品・その向き（`BUY`/`SELL`）に掛ける率（%。0 で触らない）。"""
    kind = KIND_BY_KEY.get(event.get("kind")) if isinstance(event, dict) else None
    if kind is None or event.get("status") != ACTIVE:
        return 0
    stage = min(max(_int(event.get("stage"), 1), 1), STAGES[-1])
    total = 0
    for target, side, rates in kind["effects"]:
        types = _targets(target, event)
        if types is not None and category not in types:
            continue
        if side == "both" or (side == "buy" and key == BUY) or (side == "sell" and key == SELL):
            total += rates[stage - 1]
    return total * max(0, int(strength_percent)) / 100.0


def inn_rate(event, strength_percent=100):
    kind = KIND_BY_KEY.get(event.get("kind")) if isinstance(event, dict) else None
    if kind is None or event.get("status") != ACTIVE or not kind.get("inn"):
        return 0
    stage = min(max(_int(event.get("stage"), 1), 1), STAGES[-1])
    return kind["inn"][stage - 1] * max(0, int(strength_percent)) / 100.0


def scaled(price, rate_percent):
    """率を掛けた額。0 を下回らない。率が 0 なら None（触らない）。"""
    if not rate_percent:
        return None
    return max(0.0, float(price) * (1.0 + rate_percent / 100.0))


# ------------------------------------------------------------ 文
def text_of(event, name, **fields):
    """LLM が書いた文、無ければ表の文。`{town}` と `{goods}` を埋める。"""
    kind = KIND_BY_KEY.get(event.get("kind"), {})
    written = (event.get("text") or {}).get(name)
    if not written:
        if name == "name":
            written = kind.get("name", "")
        elif name == "summary":
            written = kind.get("what", "") + "。" if kind.get("what") else ""
        elif name.startswith("arrive_"):
            stage = _int(int(name[-1]) if name[-1].isdigit() else 1, 1)
            arrive = kind.get("arrive") or ("",)
            written = arrive[min(stage, len(arrive)) - 1]
        else:
            written = kind.get(name, "")
    fields.setdefault("town", event.get("area_name") or "その街")
    fields.setdefault("goods", GOODS_NAMES.get(event.get("goods"), "ある品"))
    try:
        return str(written).format(**fields)
    except (KeyError, IndexError, ValueError):
        return str(written)


def arrive_text(event):
    return text_of(event, "arrive_{}".format(_int(event.get("stage"), 1)))


#: 窓の日付の出し方（設定 `WINDOW_DAYS` の値）。
DAYS_NUMBER = "日数"
DAYS_VAGUE = "あいまい"
#: 窓の影響の出し方（設定 `WINDOW_EFFECTS` の値）。
EFFECTS_NUMBER = "数字"
EFFECTS_WORDS = "言葉"
EFFECTS_HIDDEN = "出さない"


def days_in_words(days):
    """次に段が変わるまでの日数を、あいまいな言葉にする。"""
    if days is None:
        return "先は分からないが、"
    if days <= 15:
        return "じきに"
    if days <= 45:
        return "しばらく先に"
    return "まだ先に"


def since_in_words(days):
    """起きてからの日数を、あいまいな言葉にする。"""
    if days is None:
        return "いつからかは分からない"
    if days <= 15:
        return "起きたばかり"
    if days <= 60:
        return "少し前から続いている"
    return "長く続いている"


def ago_in_words(days):
    """終わってからの日数を、あいまいな言葉にする。"""
    if days is None:
        return "いつか"
    if days <= 30:
        return "最近"
    if days <= 120:
        return "少し前"
    return "だいぶ前"


def effect_parts(event, strength_percent=100, mode=EFFECTS_NUMBER):
    """その出来事のいまの段の値段の効き目を、窓に出す短い句の並びにする。出さないなら空。"""
    if mode == EFFECTS_HIDDEN:
        return []
    kind = KIND_BY_KEY.get(event.get("kind")) if isinstance(event, dict) else None
    if kind is None:
        return []
    stage = min(max(_int(event.get("stage"), 1), 1), STAGES[-1])
    scale = max(0, int(strength_percent)) / 100.0
    parts = []
    for target, side, rates in kind["effects"]:
        rate = int(round(rates[stage - 1] * scale))
        if not rate:
            continue
        if target is ALL:
            what = "全品"
        elif target == GOODS:
            what = GOODS_NAMES.get(event.get("goods"), "ある品")
        else:
            what = "・".join(GOODS_NAMES.get(name, name) for name in target)
        if mode == EFFECTS_WORDS:
            up = rate > 0
            parts.append({"buy": "{}が値上がり" if up else "{}が値下がり",
                          "sell": "{}が高く売れる" if up else "{}が安くしか売れない",
                          "both": "{}が値上がり（売っても高い）" if up
                          else "{}が値下がり（売っても安い）"}[side].format(what))
        else:
            label = {"buy": "の買値", "sell": "の売値", "both": "の売り買い"}[side]
            parts.append("{}{} {:+d}%".format(what, label, rate))
    inn = kind.get("inn")
    if inn:
        rate = int(round(inn[stage - 1] * scale))
        if rate:
            parts.append("宿代が値上がり" if mode == EFFECTS_WORDS else "宿代 {:+d}%".format(rate))
    return parts


def view_lines(events, day, rival, *, stage_days, settle_days, ended_days, in_words=False,
               quest_status=None, effects=EFFECTS_NUMBER, strength_percent=100, guild_names=None):
    """状況を見る窓の行。進行中（答え待ちは出さない）と、`ended_days` 日以内に終わったもの。

    `in_words` が真なら日付をあいまいな言葉にする（設定 `WINDOW_DAYS`）。
    `effects` は値段の効き目の出し方（`EFFECTS_NUMBER` / `EFFECTS_WORDS` / `EFFECTS_HIDDEN`）。
    `guild_names` は `{土地 id: ギルドの名前}`。無い街は「その街のギルド」と書く。
    """
    guild_names = guild_names if isinstance(guild_names, dict) else {}
    rival = rival if isinstance(rival, dict) else {}
    target = rival.get("target") if isinstance(rival.get("target"), dict) else {}
    rival_name = (rival.get("rival") or {}).get("name") if isinstance(rival.get("rival"), dict) else ""
    active = sorted(events_of({"events": events}, ACTIVE),
                    key=lambda event: (-(_int(event.get("stage"), 1)), _int(event.get("started"), 0)))
    lines = []
    if active:
        lines.append("進行中")
    for event in active:
        stage = _int(event.get("stage"), 1)
        lines.append("{}　{}（{}）".format(event.get("area_name") or "?", text_of(event, "name"),
                                       STAGE_NAMES.get(stage, "")))
        summary = text_of(event, "summary")
        if summary:
            lines.append("概要: {}{}".format(summary, "" if summary.endswith("。") else "。"))
        since = (day - _int(event.get("started"), day)) if day is not None else None
        left = days_to_next(event, day, stage_days, settle_days)
        what = "収まる" if stage >= STAGES[-1] else "悪化する"
        if in_words:
            lines.append("{}。{}{}かもしれない。".format(since_in_words(since), days_in_words(left), what))
        else:
            lines.append("発生から{}日。{}まで約{}日。".format(
                since if since is not None else "?", what, left if left is not None else "?"))
        parts = effect_parts(event, strength_percent, effects)
        if parts:
            lines.append("影響: {}。".format("、".join(parts)))
        quest = event.get("quest")
        if not quest:
            guild = guild_names.get(str(event.get("area"))) or "その街のギルド"
            lines.append("依頼: まだ出ていない（{}で掲示板を開くと出る）。".format(guild))
        elif target and str(target.get("quest")) == str(quest):
            lines.append("依頼: 掲示板に出ている。冒険者{}が狙っている。".format(rival_name or "誰か"))
        elif quest_status is not None and quest_status(quest) is None:
            lines.append("依頼: 掲示板から消えている。")
        else:
            lines.append("依頼: 掲示板に出ている。")
        lines.append("")
    ended = [event for event in events_of({"events": events}, ENDED)
             if day is None or day - _int(event.get("ended"), day) <= ended_days]
    ended.sort(key=lambda event: -_int(event.get("ended"), 0))
    if ended:
        lines.append("この1年に終わったもの")
    for event in ended:
        how = {END_PLAYER: "解決した",
               END_RIVAL: "冒険者{}が解決した".format(rival_name or ""),
               END_NATURAL: "自然に収まった"}.get(event.get("end_by"), "終わった")
        ended_day = _int(event.get("ended"))
        if in_words:
            when = ago_in_words(day - ended_day if day is not None and ended_day is not None else None)
        else:
            when = "{}日目".format(ended_day if ended_day is not None else "?")
        lines.append("{}　{}　{}（{}）".format(event.get("area_name") or "?",
                                           text_of(event, "name"), how, when))
    while lines and not lines[-1]:
        lines.pop()
    return lines
