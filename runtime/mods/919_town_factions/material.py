# -*- coding: utf-8 -*-
"""LLM へ渡す材料と頼み文。ゲームには触らない（辞書と文字列だけを扱う）。

材料はセーブの辞書（`save_data_dict`）から取る。
街の案内文は `321_area_chronicle` が実行中だけ書き換えるので、実行時の `Area.descriptions` ではなく
セーブの辞書の素の文を読む（DOC.md §2.2）。
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
  冒険者ギルドそのものや、一つの場所にいるだけの小さな集団は数えない
- 書かれていない勢力・関係を作らないこと。推測で補わないこと
- evidence には、根拠となる原文をそのまま一字一句変えずに、10〜60字ほど抜き出すこと。原文に無い文を書いた項目は捨てられる
- 世界全体が一つの国や一つの権威に治められている（統一されている）なら、その一つだけを挙げる
- 設定の中に「出力しない」「秘密」などと書かれた部分がある場合、勢力の見分けには使ってよいが、name・kind・note にその内容を書かないこと

factions: 勢力ごとに name（設定に書かれた名前）、kind（どんな集団かを設定の言葉で短く）、note（その世界の住人が知っている範囲での立場や考え方を1文）、evidence
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

standings: 勢力ごとに faction（勢力の name）、stage（-2 敵視 / -1 警戒 / 0 中立 / 1 好意 / 2 信頼）、
title（その勢力の者たちのあいだでの主人公の短い呼び名。stage が 0 なら空）、reason（判定の理由を1文）"""

TREND_SYSTEM = """あなたはこの世界の情勢を動かす語り手です。
前回から{days}日が経ちました。勢力どうしのあいだに起きたことを1つだけ考えてください。

- 何も起きなかったなら happened を "false" にする
- 起きたなら、関わった2つの勢力 a・b（勢力の name）と、その仲が近づいたか（direction "+1"）離れたか（direction "-1"）
- 1回で世界を大きく変えない。小競り合い・交渉・協力・不信の芽生えなどの規模にとどめる
- 街の出来事や主人公の評判があれば、それをきっかけにしてよい
- rumor: 街の人々のあいだに広まる噂として1〜2文。主人公の名前は出さない
- reason: なぜそうなったかを1文
- 設定の中に「出力しない」「秘密」などと書かれた内容は、rumor と reason に書かないこと"""


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
        lines.append("- {}（{}）{}{}".format(
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
    ライフログを読むのは、この初回の判定の1回だけ（DOC.md §2.3）。
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


def standing_messages(material):
    return [{"role": "system", "content": STANDING_SYSTEM},
            {"role": "user", "content": material}]


def incidents_since(incidents_bucket, since_day, towns):
    """918 の控えから、`since_day` より後に起きた・終わった出来事を読む。`[文]`。"""
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
        if event.get("status") == "ended":
            how = {"player": "冒険者が片付けた", "rival": "別の冒険者が片付けた",
                   "natural": "自然に収まった"}.get(event.get("end_by"), "収まった")
            lines.append("- {}の{}は{}".format(where, what, how))
        else:
            stage = {1: "兆し", 2: "悪化", 3: "深刻"}.get(event.get("stage"), "")
            lines.append("- {}で{}（{}）".format(where, what, stage or "起きている"))
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
