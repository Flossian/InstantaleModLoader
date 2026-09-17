# -*- coding: utf-8 -*-
r"""建物の主に据える人。管理人（大家）を1人作り、据えられなければ役人を借りる。

建物そのものはローダの `modfacility`（TECH.md §5.8）が建てる。
ここに残るのは「その建物の主に誰を据えるか」だけで、これは施設ではなく人の話。

ゲームの宿泊は**主を世界の名簿から引く**（`world.characters[owner]`）。
主のいない施設で起こすと `KeyError: None` でワーカースレッドごと落ち、
画面は「…」のまま戻らない（VERIFICATION.md §3.62）。

## 主は契約ごとに立てた管理人

契約1つにつき管理人を1人立て、その人を建物の主にする（`modnpc`。TECH.md §5.7）。
立場は契約の種類で呼び分ける。

    賃貸（rent）    大家    物件を貸している側。家賃を受け取る
    建売（owned）   管理人  買った家の留守を預かる、雇われた側

素性は**契約を結んだ1回だけ**ゲームと同じ生成 AI に作らせ、契約の控えに持つ
（ロードのたびに聞き直さない）。作れなければ `KEEPER_POOL` の表から選ぶ。

役人を借りる `owner_candidate` は、その管理人が引けないときの逃げ道として残す。
借り物を主に据えると、宿の主人を見る MOD（`327_inn_quality`）が
その役人を宿の主人と読み、自宅に泊まるたびに役人との好感度が上がった
（VERIFICATION.md §3.62）。
"""

import hashlib

from instantale_modloader import ui

#: 契約の相手（役場）の `facility_type`。その主が逃げ道の大家になる。
OFFICE_FACILITY_TYPE = "administrative_office"

# ---------------------------------------------------------------- 立場
#: 契約の種類ごとの呼び名。買った家に大家は居ない（雇った管理人が留守を預かる）。
ROLE_LABELS = {"rent": "大家", "owned": "管理人"}
ROLE_FALLBACK = "管理人"

#: 相手から見たプレイヤー（`relationship["player"]["relationship"]` に入れる語）。
PLAYER_ROLE = {"rent": "店子", "owned": "雇い主"}


def role_of(kind):
    """契約の種類から立場の呼び名。"""
    return ROLE_LABELS.get(str(kind), ROLE_FALLBACK)


def player_role_of(kind):
    """その管理人から見たプレイヤーの呼び名。"""
    return PLAYER_ROLE.get(str(kind), "雇い主")


# ---------------------------------------------------------------- 表
#: 生成 AI が使えないときに選ぶ12人。`(名前, 老若男女, 話し方, 人柄)`。
#: `category` は実セーブの NPC が使う語のうち人間の6語（GAME.md §2.23）。
#: 6人だと物件が増えたときに名前が重なるので、`keeper_choice` が
#: **その世界で使っていない名前**まで送れるだけの数を置く。
KEEPER_POOL = (
    ("ヨナス", "middle-aged man", "低い声でぼそぼそと話す", "戸締まりと雨漏りにうるさい"),
    ("ルチア", "young woman", "明るく早口", "近所の噂をよく知っている"),
    ("アルベル", "old man", "ゆっくり間を置いて話す", "この区画の建物を長く見てきた"),
    ("ミレイユ", "middle-aged woman", "丁寧だが有無を言わせない", "家賃の取り立てに容赦が無い"),
    ("グスタフ", "old man", "短く言い切る", "昔は石工で、自分で直せるものは自分で直す"),
    ("ロヴィーサ", "young woman", "小声で言葉を選ぶ", "人見知りだが頼まれた仕事は違えない"),
    ("テオドル", "young man", "愛想がよく口が回る", "商売気が強く、何かと物を売りつけたがる"),
    ("カリン", "middle-aged woman", "きびきびと短く話す", "掃除と帳面がいつも行き届いている"),
    ("ボリス", "middle-aged man", "訛りが強く声が大きい", "力仕事を厭わず、人の出入りをよく見ている"),
    ("イルゼ", "old woman", "皮肉を混ぜて話す", "若い頃から同じ通りに住んでいる"),
    ("レンナルト", "young man", "堅苦しいほど礼儀正しい", "任された仕事を几帳面にこなす"),
    ("アガタ", "old woman", "たとえ話が多い", "この土地の古い決まりに通じている"),
)

#: 管理人のレベル。詳細生成が掛け算に使うので None にしない（`keeper_fields`）。
#: 戦う相手ではないので低め。
KEEPER_LEVEL = 3

#: プレイヤーへの好感度の初期値。40 は「多少の好意がある」の段（GAME.md §2.25.1）。
#: 会話の頼み文には `affinity` から組んだ文が毎回載るので、初対面の「警戒心がある」の
#: ままだと、家を借りている相手を警戒する大家になる。
KEEPER_AFFINITY = 40

#: 素性の文（`modnpc.register` の `fields["profile"]`）。契約の種類で立て付けが違う。
RENT_PROFILE = (
    "{area}の{house}を{player}に貸している大家。"
    "家賃を受け取り、{player}が留守のあいだも建物と鍵を預かっている。{note}。"
)
OWNED_PROFILE = (
    "{area}の{house}を預かっている管理人。"
    "家を買った{player}が雇い主で、留守のあいだの手入れと戸締まりを任されている。{note}。"
)

#: 会話の頼み文に足す一文（`modnpc` の `notes`）。相手が誰なのかを毎回伝える。
#: 老若男女の語。答えがこれ以外なら表の値へ落とす。
KEEPER_CATEGORIES = ("young man", "young woman", "middle-aged man",
                     "middle-aged woman", "old man", "old woman")

#: 管理人を作らせる頼み文。ゲーム自身の生成（`master_ai_npc_generater`）と同じ口調で、
#: こちらが要るのは4つ（名前・老若男女・話し方・人柄）と素性の文だけ。
#: 能力値・見た目・技はゲームの詳細生成が会話の直前に埋める（GAME.md §2.23）。
#: **家の名前は作らせない**（呼び名は設定で決まる。`RENT_NAME` / `OWNED_NAME`）。
KEEPER_SYSTEM = (
    "あなたはダークファンタジーRPGのキャラクター生成AIだ。\n"
    "ある家の鍵を預かる人物を1人設定しろ。\n"
    "【出力要素】\n"
    "- name: 名前。この世界の人名らしく、姓を付けず短く。\n"
    "- category: 老若男女のカテゴリ。次のどれか1つをそのまま書く: {categories}\n"
    "- speech_style: 話し方の癖を一文で。\n"
    "- personality: 性格と来歴を一文か二文で。\n"
    "- profile: 何者かを二文で。その家を預かっていること、"
    "住人が留守のあいだも建物を見ていることを含める。\n"
    "【その他】\n"
    "- 家そのものの名前は作らない。人物だけを書く。\n"
    "- 戦う相手ではない。強さや戦闘の話は書かない。\n"
)

KEEPER_ASK = (
    "【世界の情報】\n- 世界観: {world}\n"
    "【土地の情報】\n- 名前: {area}\n"
    "【家の情報】\n- 呼び名: {house}\n- 契約: {contract}\n"
    "【住人】\n- 名前: {player}\n"
    "【生成する人物】\n- 立場: {role}\n"
)

#: 契約の説明。立場の違い（貸し主か、雇われた側か）はここで伝える。
CONTRACT_TEXT = {
    "rent": "{player}が役場を通して借りている。あなたはその貸し主で、家賃を受け取る側",
    "owned": "{player}が買い取った家。あなたは雇われて留守を預かる側",
}

#: 既にこの世界に居る管理人の名前を避けさせる一文。
KEEPER_AVOID = "- 次の名前は既にこの世界の管理人が使っている。避けること: {names}\n"


def _index(seed, count):
    """鍵から 0..count-1 の1つ。同じ鍵からは同じ数。"""
    if count <= 0:
        return 0
    digest = hashlib.sha1(str(seed).encode("utf-8")).hexdigest()
    return int(digest[:8], 16) % count


def keeper_choice(seed, taken=()):
    """表から1人。`taken` に在る名前は飛ばす（同じ世界で名前を重ねない）。

    鍵から始めて並びを順に送るだけなので、**同じ鍵と同じ `taken` からは同じ人**になる。
    全部使われていたら鍵の人をそのまま返す（重なるのは仕方ない）。
    """
    used = set(str(name) for name in (taken or ()) if name)
    start = _index(seed, len(KEEPER_POOL))
    for step in range(len(KEEPER_POOL)):
        candidate = KEEPER_POOL[(start + step) % len(KEEPER_POOL)]
        if candidate[0] not in used:
            return candidate
    return KEEPER_POOL[start]


def keeper_by_name(name):
    """名前から表の候補を引く。無ければ None。"""
    for candidate in KEEPER_POOL:
        if candidate[0] == str(name):
            return candidate
    return None


def keeper_relationship(kind):
    """プレイヤーに持つ関係の初期値（実セーブの `relationship["player"]` と同じ4鍵）。

    `affinity_text` は会話のたびにゲームが `affinity` から組み直す（GAME.md §2.25.1）ので、
    ここに書く文は最初の会話までのつなぎ。
    """
    return {"player": {"affinity": KEEPER_AFFINITY,
                       "affinity_text": "多少の好意がある",
                       "relationship": [player_role_of(kind)],
                       "conversation_count": 0}}


#: 立場の一文。素性の文に必ず入れる（生成 AI の答えには入らないことがある）。
STANDING_MARK = "この家"
RENT_STANDING = "この家を借りているのは{player}で、あなたはその大家にあたる。"
OWNED_STANDING = "この家を買ったのは{player}で、あなたにとっては雇い主にあたる。"


def standing_line(kind, player=None, house=None):
    """住人との立場を言う一文。"""
    template = RENT_STANDING if str(kind) == "rent" else OWNED_STANDING
    return template.format(player=player or "住人", house=house or "家")


def keeper_prompt(kind, area_name, house, world_overview, player, taken=()):
    """管理人を作らせる頼み文。`(system, user)`。

    `taken` はその世界で使っている管理人の名前。避けさせる。
    """
    system = KEEPER_SYSTEM.format(categories=" / ".join(KEEPER_CATEGORIES))
    names = [str(name) for name in (taken or ()) if name]
    if names:
        system += KEEPER_AVOID.format(names="、".join(names))
    contract = CONTRACT_TEXT.get(str(kind), CONTRACT_TEXT["owned"])
    user = KEEPER_ASK.format(
        world=world_overview or "", area=area_name or "この街",
        house=house or "家", contract=contract.format(player=player or "住人"),
        player=player or "住人", role=role_of(kind))
    return system, user


def keeper_fields_from(answer, kind, area_name, house, seed, player=None, taken=()):
    """生成 AI の答えを素データに均す。読めない項目は表の人で埋める。

    名前が空・既に使われている、のどちらかなら `None`（呼ぶ側が表へ降りる）。
    `category` が語彙の外なら**その項目だけ**落とす。
    """
    if not isinstance(answer, dict):
        return None
    fallback = keeper_fields(kind, area_name, house, seed, player=player, taken=taken)
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
    # **立場は必ず素性の文に入れる。** 生成 AI が書いた素性には、
    # 相手が店子なのか雇い主なのかが入らないことがある（実測）。
    # 滞在の描写を書くのはこの文を読む側なので、こちらで1文添える。
    if STANDING_MARK not in fields.get("profile", ""):
        fields["profile"] = "{} {}".format(
            fields.get("profile", ""), standing_line(kind, player, house)).strip()
    return fields


def keeper_fields(kind, area_name, house, seed, player=None, taken=(), name=None):
    """管理人の素データ（`modnpc.register` の `fields`）。

    `player` は住人（プレイヤー）の名。
    `name` を渡すとその人で組む（控えに持った管理人。建て直しで名前が変わらない）。
    渡さなければ鍵から選び、`taken` に在る名前は飛ばす。
    """
    chosen = keeper_by_name(name) if name else None
    name, category, speech, note = chosen or keeper_choice(seed, taken)
    template = RENT_PROFILE if str(kind) == "rent" else OWNED_PROFILE
    return {
        "name": name,
        "category": category,
        "job": role_of(kind),
        "profile": template.format(area=area_name or "この街",
                                   house=house or "家",
                                   player=player or "住人", note=note),
        "relationship": keeper_relationship(kind),
        "personality": note,
        "speech_style": speech,
        "look_description": None,
        "age": 40,
        # 詳細生成（会話の直前に本体が能力値を埋める）は `experience_level` を掛け算に使う。
        # None だと `calculate_attribute` が `int * None` で落ち、会話のスレッドが死んで
        # 画面が「…」のまま戻らない（VERIFICATION.md §3.62）。
        "experience_level": KEEPER_LEVEL,
    }


# ---------------------------------------------------------------- 逃げ道
def id_list_of(holder, *names):
    """`characters` / `resident_npcs` のような id の配列を読む。重複は落とす。"""
    found = []
    for name in names:
        value = getattr(holder, name, None)
        if not isinstance(value, (list, tuple, set)):
            continue
        for item in value:
            key = str(item)
            if key and key not in found:
                found.append(key)
    return found


def office_owner(area, roster):
    """その土地の役場の主の id。名簿に居なければ None。"""
    for node in ui.nodes_of(area):
        for facility in ui.facilities_of(node).values():
            if ui.facility_type_of(facility) != OFFICE_FACILITY_TYPE:
                continue
            owner = getattr(facility, "owner", None)
            if owner is not None and str(owner) in roster:
                return str(owner)
    return None


def owner_candidate(app, area=None, facility=None, write=None):
    """`facility.owner` に据えられる character id。見つからなければ None。

    **管理人が立てられなかったときの逃げ道**（主のいない施設で宿泊を起こすと落ちる）。
    据えるのはその土地の役場の主で、物件を貸したのも売ったのも役場だから。
    引けない土地のために、その建物・その土地の住人へ順に落ちる
    （**名簿に在る id しか返さない**。在らぬ id を据えると同じ `KeyError` になる）。
    """
    roster = getattr(getattr(app, "world", None), "characters", None)
    if not isinstance(roster, dict) or not roster:
        if write:
            write("WARN owner: the world has no character roster")
        return None
    key = office_owner(area, roster)
    if key is not None:
        return key
    for holder in (facility, area):
        if holder is None:
            continue
        for item in id_list_of(holder, "characters", "resident_npcs",
                               "adventurer_npcs"):
            if item in roster:
                if write:
                    write("owner: no clerk in the office; borrowing {!r} from {}"
                          .format(item, type(holder).__name__))
                return item
    key = str(next(iter(roster)))
    if write:
        write("owner: nobody else was reachable; borrowing {!r}".format(key))
    return key


def at_facility_type(app, kind):
    """いま立っているのがその種類の施設か（役場の窓口を出すため）。"""
    location = getattr(getattr(app, "player", None), "location", None)
    if location is None or isinstance(location, (str, int)):
        return False
    return ui.facility_type_of(location) == kind
