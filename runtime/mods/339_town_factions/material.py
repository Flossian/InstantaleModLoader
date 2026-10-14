# -*- coding: utf-8 -*-
"""LLM へ渡す材料と頼み文。ゲームには触らない（辞書と文字列だけを扱う）。

材料はセーブの辞書（`save_data_dict`）から取る。
街の案内文は `321_area_chronicle` が実行中だけ書き換えるので、実行時の `Area.descriptions` ではなく
セーブの辞書の素の文を読む（VERIFICATION.md §3.98「元の案内文を読む」）。
"""
from . import rules

TOWN_SIZES = ("village", "town", "city")
OVERVIEW_LIMIT = 6000
LIFE_LOG_LIMIT = 4000
ACHIEVEMENT_LIMIT = 3000
RECENT_EVENTS = 5

EXTRACT_SYSTEM = """あなたは物語の設定を読む編集者です。
下の【世界の設定】と【街の一覧】に書かれていることだけから、この世界の勢力を抜き出してください。

- 勢力とは、国・教団・組織・一族など、複数の人々を束ねて利害を持つ集団のこと。
  設定の中で一つのまとまりとして名指しされている集団だけを挙げる。迷ったら挙げない
- 職業・業種・身分の呼び名は数えない。同じ仕事や身分の人々・業者をまとめて呼んだもの（警備会社・金貸し・漁り屋・武装集団・自警団・貴族など、
  別々の集団がいくつもありうる呼び名）は、設定がそれを一つの組織として書いていない限り勢力ではない
- 街の説明から呼び名を組み立てない（「〇〇の支配勢力」など）。name は設定に書かれたとおりの呼び名にする
- 一つの場所にいるだけの小さな集団は数えない
- 主人公のような冒険者に当たる人々と、その組合や斡旋所は数えない（その世界での呼び名が何であれ）。
  設定に「冒険者の代わりに〜がいる」「冒険者に当たる〜」と書かれた人々もこれに入る
- 一人の人物や一つの役職は数えない（その人物や役職が属する集団が書かれていれば、集団のほうを挙げる）
- 書かれていない勢力・関係を作らないこと。推測で補わないこと
- evidence には、根拠となる原文をそのまま一字一句変えずに、10〜60字ほど抜き出すこと。原文に無い文を書いた項目は捨てられる
- 世界全体が一つの国や一つの権威に治められている（統一されている）なら、その一つだけを挙げる
- 設定の中に「出力しない」「秘密」などと書かれた部分がある場合、勢力の見分けには使ってよいが、name・kind・note にその内容を書かないこと

factions: 勢力ごとに name（設定に書かれた名前）、
name_type（設定がその名前で、この世界の特定の一つの組織を指しているなら "固有"。「教会」「商会」のような普通の言葉の名前でも、設定がこの世界の一つの勢力として書いていれば "固有"。
同じ種類の人々・業者・集団をまとめて呼んだ一般の呼び名なら "一般"）、
kind（どんな集団かを設定の言葉で短く）、note（その世界の住人が知っている範囲での立場や考え方を1文）、evidence
towns: その街で力を持つ勢力が書かれている街だけ、town_id・faction（factions の name）・evidence。書かれていない街は挙げない
relations: 勢力どうしの関係が書かれていれば a・b（factions の name）・stage（-2 敵対 / -1 緊張・対立 / 0 中立 / 1 友好 / 2 同盟）・evidence
peaceful: 世界観が争いの無い穏やかなものなら "true"、そうでなければ "false\""""

STANDING_SYSTEM = """あなたは物語の語り手です。
下の主人公を、この世界の各勢力がどう見ているかを判定してください。

- その勢力が知りえたことだけで判断すること。主人公の経歴のうち、人に知られていない部分（本人しか知らない過去など）は使わない
- 手配度が 0 未満の街は、主人公が罪を犯して追われている街。その街で力を持つ勢力は主人公を悪く見る
- 功績は、その街で主人公がしたこと
- 材料が乏しい勢力は 0（中立）にする
- 設定の中に「出力しない」「秘密」などと書かれた内容は、title と reason に書かないこと

standings: 勢力ごとに faction（勢力の name）、stage（-2 敵対 / -1 緊張 / 0 中立 / 1 友好 / 2 盟友）、
title（その勢力の者たちのあいだでの主人公の短い呼び名。stage が 0 なら空）、reason（判定の理由を1文）"""

DESIGN_SYSTEM = """あなたは物語の設定を作る編集者です。
下の【世界の設定】と【街の一覧】は、すでに遊ばれている世界です。この世界に、設定と矛盾しない組織を2〜4つ設けてください。

- 組織とは、国・教団・企業・結社・一族など、固有の名前を持つ一つのまとまり。同じ仕事や身分の人々をまとめた呼び名（〜屋・〜業者など）は組織にしない
- 冒険者（またはそれに当たる人々）とそのギルド・組合は組織にしない
- 設定にすでに名前の出ている集団があれば、それを組織として使ってよい。新しく設けるなら、街の案内文や土地柄から無理なく生まれる組織にする
- 数は世界観に合わせて決める。組織の名前はこの世界の言葉で付け、どの世界にもありそうな名前に寄せない
- 設定の中に「出力しない」「秘密」などと書かれた内容は、name・kind・note に書かないこと

factions: 組織ごとに name、kind（どんな集団かを短く）、note（その世界の住人が知っている範囲での立場や考え方を1文）、
towns（その組織が力を持つ街の town_id の並び。どの組織にも属さない街があってもよい。一つの街は一つの組織だけ）
relations: 組織どうしの関係 a・b（name）・stage（-2 敵対 / -1 緊張・対立 / 0 中立 / 1 友好 / 2 同盟）
peaceful: 世界観が争いの無い穏やかなものなら "true"、そうでなければ "false\""""

ADJUST_SYSTEM = """あなたは物語の語り手です。
下の主人公を、この世界の各勢力がどう見ているかを決めます。
土台の段階は、その勢力が力を持つ街での主人公の功績・滞在・手配から、すでに決めてあります。
功績の中身と主人公の経歴を読み、その勢力の考えや利益に照らして土台を動かすべき事情があるかだけを判定してください。

- その勢力が知りえたことだけで判断すること。主人公の経歴のうち、人に知られていない部分は使わない
- adjust: 土台より良く見る事情があれば 1、悪く見る事情があれば -1、どちらでもなければ 0
- 迷ったら 0 にする
- 設定の中に「出力しない」「秘密」などと書かれた内容は、title と reason に書かないこと

standings: 勢力ごとに faction（勢力の name）、adjust、title（その勢力の者たちのあいだでの主人公の短い呼び名。最後の段階が中立なら空）、reason（1文）"""

TREND_SYSTEM = """あなたはこの世界の情勢を動かす語り手です。
前回から{days}日が経ちました。勢力どうしのあいだに起きたことを1つだけ考えてください。

- 何も起きなかったなら happened を "false" にする
- 起きたなら happened を "true" にし（出来事の中身は rumor と reason に書く）、関わった2つの勢力 a・b（勢力の name）と、その仲が近づいたか（direction "+1"）離れたか（direction "-1"）
- 1回で世界を大きく変えない。小競り合い・交渉・協力・不信の芽生えなどの規模にとどめる
- 街の出来事や主人公の評判があれば、それをきっかけにしてよい
- rumor: 街の人々のあいだに広まる噂として1〜2文。主人公の名前は出さない
- reason: なぜそうなったかを1文
- 設定の中に「出力しない」「秘密」などと書かれた内容は、rumor と reason に書かないこと"""


#: 世界の生成（`create_world_overview`）の頼みに足す段。【注意】の前に入れる（無ければ末尾）。
#: 地理の説明（`structure_description`）と拠点の概要文（`settlement_overview` → 街の案内文）に書かせる。
#: 世界の概要には書かせない（`145_fix_world_overview` が入力どおりの文に差し替えるため）。
WORLD_ORGANIZATIONS = """【勢力】
- この世界の世界観に沿った組織を2〜4つ設ける。数は世界観に合わせて決める
- 組織とは、国・教団・企業・結社・一族など、固有の名前を持つ一つのまとまり。同じ仕事や身分の人々をまとめた呼び名（〜屋・〜業者など）は組織にしない
- 冒険者（またはそれに当たる人々）とそのギルド・組合は組織にしない。ギルドに組織のような名前を付けない
- 組織の名前はこの世界の言葉で付け、どの世界にもありそうな名前に寄せない
- 手順2の地理の説明（structure_description）の後半に、各組織の名前・性格・力の及ぶ地域・組織どうしの関係（対立・協力・緊張など）を書く
- 各エリアの拠点の概要文に、その拠点で力を持つ組織の名前を書く。どの組織にも属さない拠点があってもよい
- 組織はこの世界の住人が知っている存在として書く
"""
NOTICE_HEAD = "【注意】"


def with_world_organizations(text):
    """世界の生成の頼み文に【勢力】の段を足した文。足す先が無ければ None。足し済みなら None。"""
    if not isinstance(text, str) or "【構造のルール】" not in text or "【勢力】" in text:
        return None
    at = text.find(NOTICE_HEAD)
    if at < 0:
        return text.rstrip() + "\n\n" + WORLD_ORGANIZATIONS
    return text[:at] + WORLD_ORGANIZATIONS + "\n" + text[at:]


def towns_of(save_data_dict):
    """`[(id, 名前, 規模, {overview, facilities, area_description})]`。街（村・町・都市）だけ。"""
    areas = (save_data_dict or {}).get("areas")
    if not isinstance(areas, dict):
        return []
    towns = []
    for area_id, area in areas.items():
        if not isinstance(area, dict) or area.get("size") not in TOWN_SIZES:
            continue
        descriptions = area.get("descriptions") if isinstance(area.get("descriptions"), dict) else {}
        towns.append((str(area_id), str(area.get("name") or area_id), area.get("size"),
                      {key: str(descriptions.get(key) or "").strip()
                       for key in ("overview", "facilities", "area_description")}))
    return sorted(towns, key=lambda town: (len(town[0]), town[0]))


def world_data_of(save_data_dict):
    data = (save_data_dict or {}).get("world_data")
    return data if isinstance(data, dict) else {}


def _cut(text, limit):
    text = str(text or "").strip()
    return text if len(text) <= limit else text[:limit] + "…"


def _tail(text, limit):
    text = str(text or "").strip()
    return text if len(text) <= limit else "…" + text[-limit:]


def extract_material(save_data_dict):
    """抽出に渡す材料の文。根拠の照合にもこの文を使う。"""
    world = world_data_of(save_data_dict)
    story = world.get("story") if isinstance(world.get("story"), dict) else {}
    lines = ["【世界の設定】", _cut(world.get("overview"), OVERVIEW_LIMIT)]
    if world.get("structure_description"):
        lines += ["", "【地理】", str(world["structure_description"]).strip()]
    if story.get("world_situation"):
        lines += ["", "【世界の情勢】", str(story["world_situation"]).strip()]
    lines += ["", "【街の一覧】"]
    for town_id, name, size, descriptions in towns_of(save_data_dict):
        lines.append("- town_id {}: {}（{}）".format(town_id, name, size))
        for key in ("overview", "facilities", "area_description"):
            if descriptions[key]:
                lines.append("  " + descriptions[key])
    return "\n".join(lines)


def extract_messages(material):
    return [{"role": "system", "content": EXTRACT_SYSTEM},
            {"role": "user", "content": material}]


def factions_block(ledger, towns):
    """勢力・街・関係の一覧の文（判定と趨勢の頼みに使う）。"""
    names = {town_id: name for town_id, name, _size, _d in towns}
    lines = ["【勢力】"]
    for faction in ledger.get("factions") or []:
        held = [names.get(town_id, town_id) for town_id, row in (ledger.get("towns") or {}).items()
                if row.get("faction") == faction["id"]]
        # 名前の後ろに括弧を付けない（LLM が「名前（種類）」の形で写して返した。2026-10-11 の実機）
        lines.append("- {}: {}。{}{}".format(
            faction["name"], faction.get("kind") or "勢力", faction.get("note") or "",
            " 力の強い街: {}".format("・".join(held)) if held else ""))
    relations = []
    by_id = {f["id"]: f["name"] for f in ledger.get("factions") or []}
    for key, row in sorted((ledger.get("relations") or {}).items()):
        a, b = key.split("|")
        relations.append("- {} と {}: {}".format(by_id.get(a, a), by_id.get(b, b),
                                                 rules.RELATION_NAMES.get(row.get("stage", 0))))
    if relations:
        lines += ["", "【勢力どうしの関係】"] + relations
    return "\n".join(lines)


def standing_material(save_data_dict, ledger, *, player_name, profile, lawfulness,
                      achievements, life_log):
    """初回の判定に渡す材料の文。

    `lawfulness` は `{街の id: 手配度}`、`achievements` は `{街の id: [文]}`。
    ライフログを読むのは、この初回の判定の1回だけ（VERIFICATION.md §3.98「最初の見られ方」）。
    """
    towns = towns_of(save_data_dict)
    names = {town_id: name for town_id, name, _size, _d in towns}
    world = world_data_of(save_data_dict)
    lines = ["【世界の設定】", _cut(world.get("overview"), OVERVIEW_LIMIT), "",
             factions_block(ledger, towns), "",
             "【主人公】{}".format(player_name), _cut(profile, 1500) or "（経歴の記述なし）"]
    wanted = ["- {}: {}".format(names[t], v) for t, v in sorted(lawfulness.items())
              if t in names and isinstance(v, (int, float)) and v < 0]
    lines += ["", "【手配度が 0 未満の街】"] + (wanted or ["なし"])
    deeds = []
    for town_id, texts in sorted(achievements.items()):
        if town_id in names and texts:
            deeds.append("- {}: {}".format(names[town_id], " / ".join(str(t) for t in texts)))
    lines += ["", "【功績】", _tail("\n".join(deeds), ACHIEVEMENT_LIMIT) if deeds else "なし"]
    if life_log:
        lines += ["", "【これまでの出来事（新しいものほど後ろ）】", _tail(life_log, LIFE_LOG_LIMIT)]
    return "\n".join(lines)


def design_messages(material):
    return [{"role": "system", "content": DESIGN_SYSTEM},
            {"role": "user", "content": material}]


def adjust_material(save_data_dict, ledger, baseline, records, *, player_name, profile, rules):
    """途中導入の調整に渡す材料。勢力ごとに、土台の段階と、その勢力の街での功績・滞在・手配。"""
    towns = towns_of(save_data_dict)
    names = {town_id: name for town_id, name, _size, _d in towns}
    world = world_data_of(save_data_dict)
    lines = ["【世界の設定】", _cut(world.get("overview"), OVERVIEW_LIMIT), "",
             factions_block(ledger, towns), "",
             "【主人公】{}".format(player_name), _cut(profile, 1500) or "（経歴の記述なし）", ""]
    for faction in ledger.get("factions") or []:
        score, facts = baseline.get(faction["id"], (0, {}))
        held = [t for t, row in (ledger.get("towns") or {}).items() if row.get("faction") == faction["id"]]
        lines.append("【{}】土台の段階: {}（その勢力の街での功績 {} 件・滞在 {} 日{}）".format(
            faction["name"], rules.STANDING_NAMES[rules.standing_stage(score)], facts.get("deeds", 0),
            facts.get("days", 0), "・手配度 {}".format(facts["law"])
            if facts.get("law") is not None and facts["law"] < 0 else ""))
        deeds = []
        for town in held:
            for text in (records.get(town) or {}).get("deeds") or []:
                deeds.append("- {}: {}".format(names.get(town, town), text))
        lines.append(_tail("\n".join(deeds), ACHIEVEMENT_LIMIT // 2) if deeds else "（その街での功績なし）")
        lines.append("")
    return "\n".join(lines).rstrip()


def adjust_messages(material):
    return [{"role": "system", "content": ADJUST_SYSTEM},
            {"role": "user", "content": material}]


def standing_messages(material):
    return [{"role": "system", "content": STANDING_SYSTEM},
            {"role": "user", "content": material}]


# ============================================================ 住人の立場
PEOPLE_SYSTEM = """あなたは物語の設定を読む編集者です。
ある街の住人一人ひとりが、その街で力を持つ勢力とどういう関係にあるかを、住人の人物紹介から判断します。

people: 住人ごとに id（住人の一覧の id）、reason、role、faction、open
reason: 人物紹介のどこから、その街の勢力とどういう関係だと読んだかを1文（先に書き、role はこれに合わせる）
role は次のどれか1つ（英語のまま書く）:
- member: その街の勢力（【街】に書いた勢力）に属し、一員として振る舞う（勢力での役目・務め・信仰・忠誠などで結び付いている）。別の勢力に属する者は member ではなく other
- follower: 一員ではないが、治める側として受け入れて従っている。とくに反感も心酔も書かれていない普通の住人
- dissenter: 表向きは従っているが、内心ではその勢力そのものに反感・不満・疑いを持っている（追われた・虐げられた・腐敗を嫌うなど）
- indifferent: その勢力の権威や考えに関心がなく、損得や自分の暮らし・好きなことだけで動く
- other: その街の勢力ではなく、別の勢力に属している・仕えている・通じている（別の勢力の一員としてこの街に置かれた者も、ひそかに通じる者も）
faction: この住人が属している・仕えている・通じている勢力の name を【勢力】から書く（その街の勢力でも、別の勢力でも）。どの勢力とも結び付きが書かれていなければ空
open: faction が別の勢力のときだけ、その勢力の者だと周りに知られている（公然とその勢力の者としてこの街にいる）なら "true"、隠しているなら "false"。ほかは空

決まり:
- 人物紹介に書かれたことだけで判断する。勢力との関わりが読み取れない住人は follower
- 職業や施設の主であることだけで member にしない（その街の店主や宿の主でも、勢力の一員とは限らない）
- 勢力の務めに就いている者は、自分自身への迷いや負い目があっても member（勢力そのものへの反感が書かれているときだけ dissenter）
- other と別の勢力の faction は、別の勢力との結び付き（属する・仕える・務める・通じる）が人物紹介に書かれているときだけ。考えや好みが近いだけ、過去に属していて今は離れた（出奔した・追われた・除籍された）だけでは書かない
- 反感や不満が書かれていなければ dissenter にしない
- dissenter の反感は、その街の勢力そのもの、またはその勢力の役人・教え・やり方・要求に向いたもの（態度や行いに表れていればよい）。【勢力】のどれでもない組織や個人への恨みは数えない
- 一覧のすべての住人について1つずつ書く"""
PERSON_PROFILE_LIMIT = 400


def home_of(save_data_dict, npc_id):
    """住人の街。名簿（住人・冒険者）に載っていればその街、無ければ生まれた街（`initial_location.area`）。

    名簿に載るのは冒険者だけで、店や施設の主は載らない（2026-10-14 にセーブで確かめた）。
    """
    npc_id = str(npc_id)
    for area_id, area in ((save_data_dict or {}).get("areas") or {}).items():
        if not isinstance(area, dict):
            continue
        ids = list(area.get("resident_npcs") or []) + list(area.get("adventurer_npcs") or [])
        if npc_id in [str(x) for x in ids]:
            return str(area_id)
    npc = ((save_data_dict or {}).get("npcs") or {}).get(npc_id)
    where = npc.get("initial_location") if isinstance(npc, dict) else None
    area = where.get("area") if isinstance(where, dict) else None
    return str(area) if area not in (None, "") else ""


def residents_of(save_data_dict):
    """街ごとの住人 `{town_id: [npc_id]}`（`home_of` の分け方）。"""
    out = {}
    for npc_id in ((save_data_dict or {}).get("npcs") or {}):
        home = home_of(save_data_dict, npc_id)
        if home:
            out.setdefault(home, []).append(str(npc_id))
    return out


def people_material(save_data_dict, ledger, towns, town_id, npc_ids):
    """住人の立場を聞く材料（勢力の一覧・街・住人の人物紹介）。"""
    names = {t_id: name for t_id, name, _size, _d in towns}
    faction = rules.faction_by_id(ledger, rules.faction_of_town(ledger, town_id)) or {}
    npcs = (save_data_dict or {}).get("npcs") or {}
    lines = [factions_block(ledger, towns), "",
             "【街】{}（この街で力を持つ勢力: {}）".format(names.get(str(town_id), town_id), faction.get("name", "")),
             "", "【住人】"]
    for npc_id in npc_ids:
        npc = npcs.get(str(npc_id)) if isinstance(npcs, dict) else None
        if not isinstance(npc, dict):
            continue
        lines.append("- id {}: {}（{}）".format(npc_id, npc.get("name") or "?", npc.get("job") or ""))
        for key, label in (("profile", "人物"), ("personality", "性格")):
            text = _cut(str(npc.get(key) or "").strip(), PERSON_PROFILE_LIMIT)
            if text:
                lines.append("  {}: {}".format(label, text))
    return "\n".join(lines)


def people_messages(material):
    return [{"role": "system", "content": PEOPLE_SYSTEM},
            {"role": "user", "content": material}]


def incidents_since(incidents_bucket, since_day, towns):
    """338 の控えから、`since_day` より後に起きた・終わった出来事を読む。`[文]`。"""
    names = {town_id: name for town_id, name, _size, _d in towns}
    lines = []
    for event in (incidents_bucket or {}).get("events") or []:
        if not isinstance(event, dict):
            continue
        started, ended = event.get("started"), event.get("ended")
        fresh = [day for day in (started, ended, event.get("stage_day"))
                 if isinstance(day, (int, float)) and (since_day is None or day > since_day)]
        if not fresh:
            continue
        text = event.get("text") if isinstance(event.get("text"), dict) else {}
        what = text.get("name") or event.get("kind") or "出来事"
        where = names.get(str(event.get("area")), event.get("area_name") or "ある街")
        good = event.get("good") is True             # 338 版2 から。依頼の無い良い出来事
        if event.get("status") == "ended":
            how = {"player": "冒険者が片付けた", "rival": "別の冒険者が片付けた",
                   "natural": "落ち着いた" if good else "自然に収まった"}.get(event.get("end_by"), "収まった")
            lines.append("- {}の{}は{}".format(where, what, how))
        else:
            stage = event.get("stage_name") or {1: "兆し", 2: "悪化", 3: "深刻"}.get(event.get("stage"), "")
            lines.append("- {}で{}（{}{}）".format(where, what, "良い出来事・" if good else "",
                                                 stage or "起きている"))
    return lines


def trend_material(save_data_dict, ledger, *, incidents, standings):
    towns = towns_of(save_data_dict)
    world = world_data_of(save_data_dict)
    lines = ["【世界の設定】", _cut(world.get("overview"), OVERVIEW_LIMIT), "",
             factions_block(ledger, towns)]
    recent = [event.get("rumor") for event in (ledger.get("events") or [])[-RECENT_EVENTS:]
              if event.get("rumor")]
    lines += ["", "【これまでの情勢（古い順）】"] + (["- " + r for r in recent] or ["なし"])
    lines += ["", "【前回からの街の出来事】"] + (incidents or ["なし"])
    lines += ["", "【主人公の評判】"] + (standings or ["なし"])
    return "\n".join(lines)


def trend_messages(material, days):
    return [{"role": "system", "content": TREND_SYSTEM.format(days=days)},
            {"role": "user", "content": material}]
