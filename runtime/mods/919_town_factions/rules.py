# -*- coding: utf-8 -*-
"""街の勢力の規則。ゲームには触らない（ゲーム抜きの検査で全部通せる）。

段の数と名前・見られ方の点数と段の対応・関係の動かし方・根拠の照合・控えの形を持つ。
決めた仕様と根拠は DOC.md。

LLM に論理は渡さない（`902_city_case` と同じ考え）。
LLM が決めるのは「設定に何が書いてあるか」「どちらへ動いたか」と文章だけで、
点数・段・幅・下限はここで決める。
"""
import re

# ============================================================ 段
#: 勢力からの主人公の見られ方。点数（-100〜100）を段に読む。
STANDING_MIN = -100
STANDING_MAX = 100
#: (この点数より下なら, 段)。最後は上限なし。
STANDING_BOUNDS = ((-60, -2), (-20, -1), (20, 0), (60, 1))
STANDING_NAMES = {-2: "敵視", -1: "警戒", 0: "中立", 1: "好意", 2: "信頼"}
#: 初回の判定の段を点数に起こすときの値（段の真ん中）。
STANDING_CENTER = {-2: -80, -1: -40, 0: 0, 1: 40, 2: 80}

#: 勢力どうしの関係の段。
RELATION_MIN = -2
RELATION_MAX = 2
RELATION_NAMES = {-2: "敵対", -1: "緊張", 0: "中立", 1: "友好", 2: "同盟"}
#: 平和な世界観での下限（戦を強制しない）。
PEACEFUL_FLOOR = -1
#: 設定に書かれた関係は、同じ向きにこの回数押されて初めて1段動く。
SETTING_INERTIA = 2

#: 会話に足す構え。NPC の側から見た文で、舞台の物（地形・制度・技術など）を書かない
#: （どの世界観でも成り立つように。人と人の関係と振る舞いだけ）。
STANCE_TEXTS = {
    -2: "あなたは{faction}に属する者として、{player}を敵とみなしている。"
        "言葉の端々に敵意と軽蔑がにじみ、{player}の頼みにはまず応じない。",
    -1: "あなたは{faction}に属する者として、{player}を信用していない。"
        "用心深く接し、必要以上のことは話さない。",
    1: "あなたは{faction}に属する者として、{player}に好意を持っている。"
       "打ち解けた態度で接し、多少の頼みなら快く聞く。",
    2: "あなたは{faction}に属する者として、{player}を身内も同然に信頼している。"
       "隠し事をせず、できる限りの便宜を図ろうとする。",
}
TITLE_TEXT = "{faction}の者たちのあいだで、{player}は「{title}」と呼ばれている。"

# ============================================================ 控えの形
LEDGER_VERSION = 1
READY = "ready"         # 台帳がある
NONE = "none"           # 設定に勢力が2つ以上書かれていない（作らない。読み込みのたびに作り直さない）
FAILED = "failed"       # 作れなかった（次の読み込みでまた試す）
EVENT_LIMIT = 30


def empty_ledger():
    return {"version": LEDGER_VERSION, "status": None, "why": "", "made_day": None,
            "peaceful": False, "factions": [], "towns": {}, "relations": {},
            "standing": {}, "events": [], "trend": {"last_day": None, "incidents": {}},
            "gained": {}}


# ============================================================ 数の均し
def to_int(value, low, high):
    """LLM の答えを low〜high の整数に均す。読めなければ None。"""
    if isinstance(value, bool):
        return None
    if isinstance(value, str):
        value = value.strip().lstrip("+")
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return max(low, min(high, int(number)))


def standing_stage(score):
    for bound, stage in STANDING_BOUNDS:
        if score < bound:
            return stage
    return 2


def clamp_score(score):
    return max(STANDING_MIN, min(STANDING_MAX, int(score)))


# ============================================================ 根拠の照合
_SPACE = re.compile(r"[\s　「」『』\"'“”‘’（）()【】]+")
EVIDENCE_MIN_CHARS = 4


def normalize(text):
    return _SPACE.sub("", str(text or ""))


def evidence_ok(evidence, material):
    """根拠の原文が材料の中に本当にあるか。空白と括弧は無視する。短すぎる根拠は認めない。"""
    quote = normalize(evidence)
    return len(quote) >= EVIDENCE_MIN_CHARS and quote in normalize(material)


# ============================================================ 台帳を組む
def pair_key(a, b):
    return "|".join(sorted((str(a), str(b))))


def build_ledger(extracted, material, town_ids, *, day=None):
    """抽出の答えから台帳を組む。根拠が材料に無いものは捨てる。

    `extracted` は LLM の答え（辞書）。`material` は渡した材料の全文（根拠の照合に使う）。
    `town_ids` はこの世界の街の id（文字列）。
    返すのは `(台帳, 捨てたものの説明の並び)`。勢力が2つ未満なら台帳の status は NONE。
    """
    ledger = empty_ledger()
    ledger["made_day"] = day
    dropped = []
    factions, by_name = [], {}
    for row in (extracted or {}).get("factions") or []:
        if not isinstance(row, dict):
            continue
        name = str(row.get("name") or "").strip()
        if not name or name in by_name:
            continue
        if not evidence_ok(row.get("evidence"), material):
            dropped.append("faction {!r}: evidence not found".format(name))
            continue
        faction = {"id": "f{}".format(len(factions) + 1), "name": name,
                   "kind": str(row.get("kind") or "").strip(),
                   "note": str(row.get("note") or "").strip(),
                   "evidence": str(row.get("evidence") or "").strip(), "source": "extracted"}
        factions.append(faction)
        by_name[name] = faction["id"]
    if len(factions) < 2:
        ledger["status"] = NONE
        ledger["why"] = "the setting names {} faction(s) with evidence".format(len(factions))
        return ledger, dropped
    ledger["status"] = READY
    ledger["factions"] = factions
    ledger["peaceful"] = str((extracted or {}).get("peaceful") or "").strip().lower() in (
        "true", "yes", "1", "はい")
    known_towns = set(str(t) for t in town_ids)
    for row in (extracted or {}).get("towns") or []:
        if not isinstance(row, dict):
            continue
        town = str(row.get("town_id") or "").strip()
        faction_id = by_name.get(str(row.get("faction") or "").strip())
        if town not in known_towns or faction_id is None:
            continue
        if not evidence_ok(row.get("evidence"), material):
            dropped.append("town {}: evidence not found".format(town))
            continue
        ledger["towns"][town] = {"faction": faction_id,
                                 "evidence": str(row.get("evidence") or "").strip()}
    for a in factions:
        for b in factions:
            if a["id"] < b["id"]:
                ledger["relations"][pair_key(a["id"], b["id"])] = {
                    "stage": 0, "push": 0, "from_setting": False, "evidence": "",
                    "updated_day": day}
    for row in (extracted or {}).get("relations") or []:
        if not isinstance(row, dict):
            continue
        a = by_name.get(str(row.get("a") or "").strip())
        b = by_name.get(str(row.get("b") or "").strip())
        stage = to_int(row.get("stage"), RELATION_MIN, RELATION_MAX)
        if a is None or b is None or a == b or stage is None:
            continue
        if not evidence_ok(row.get("evidence"), material):
            dropped.append("relation {}-{}: evidence not found".format(a, b))
            continue
        if ledger["peaceful"]:
            stage = max(PEACEFUL_FLOOR, stage)
        ledger["relations"][pair_key(a, b)].update(
            stage=stage, from_setting=True, evidence=str(row.get("evidence") or "").strip())
    for faction in factions:
        ledger["standing"][faction["id"]] = {"score": 0, "title": "", "title_stage": None,
                                            "reason": ""}
    return ledger, dropped


def apply_initial_standing(ledger, judged):
    """初回の判定（勢力ごとの段・呼び名・理由）を点数に起こす。判定の無い勢力は中立のまま。"""
    names = {f["name"]: f["id"] for f in ledger["factions"]}
    for row in (judged or {}).get("standings") or []:
        if not isinstance(row, dict):
            continue
        faction_id = names.get(str(row.get("faction") or "").strip())
        stage = to_int(row.get("stage"), -2, 2)
        if faction_id is None or stage is None:
            continue
        title = str(row.get("title") or "").strip()[:20]
        ledger["standing"][faction_id] = {
            "score": STANDING_CENTER[stage], "title": title if stage else "",
            "title_stage": stage if title and stage else None,
            "reason": str(row.get("reason") or "").strip()[:200]}
    return ledger


# ============================================================ 見られ方を動かす
def faction_of_town(ledger, town_id):
    """街の勢力の id。属していなければ None。"""
    row = (ledger.get("towns") or {}).get(str(town_id))
    return row.get("faction") if isinstance(row, dict) else None


def faction_by_id(ledger, faction_id):
    for faction in ledger.get("factions") or []:
        if faction.get("id") == faction_id:
            return faction
    return None


def relation_stage(ledger, a, b):
    row = (ledger.get("relations") or {}).get(pair_key(a, b))
    return row.get("stage", 0) if isinstance(row, dict) else 0


#: 張り合う勢力への響き。好かれた点数の何割を、相手の勢力から引くか（関係の段ごと）。
RIVAL_ECHO = {-2: 0.5, -1: 0.25}


def plan_talk(ledger, faction_id, step, *, gain_per_step, loss_per_step, gained_today):
    """会話の判定（-2〜+2）を点数の動きに起こす。`[(勢力, 前, 後, 理由)]` を返す。

    上がるのは同じ勢力から1日1回（`gained_today`）。下がる方は毎回効く。
    上がったときは、その勢力と敵対・緊張している勢力から一部を引く（`RIVAL_ECHO`）。
    """
    row = (ledger.get("standing") or {}).get(faction_id)
    if not isinstance(row, dict) or not step:
        return []
    before = row.get("score", 0)
    if step > 0:
        if gained_today:
            return [(faction_id, before, before, "daily")]
        return plan_gain(ledger, faction_id, step * max(0, int(gain_per_step)))
    after = clamp_score(before + step * max(0, int(loss_per_step)))
    return [(faction_id, before, after, "loss")]


def plan_gain(ledger, faction_id, gain, why="gain"):
    """`gain` 点上げる動き。敵対・緊張している勢力からは一部を引く（`RIVAL_ECHO`）。"""
    row = (ledger.get("standing") or {}).get(faction_id)
    if not isinstance(row, dict) or gain <= 0:
        return []
    before = row.get("score", 0)
    moves = [(faction_id, before, clamp_score(before + gain), why)]
    for other in ledger.get("factions") or []:
        other_id = other.get("id")
        share = RIVAL_ECHO.get(relation_stage(ledger, faction_id, other_id))
        other_row = (ledger.get("standing") or {}).get(other_id)
        if other_id == faction_id or not share or not isinstance(other_row, dict):
            continue
        lost = int(round(gain * share))
        if lost:
            was = other_row.get("score", 0)
            moves.append((other_id, was, clamp_score(was - lost), "echo"))
    return moves


def apply_moves(ledger, moves):
    for faction_id, _before, after, _why in moves:
        row = ledger["standing"].get(faction_id)
        if isinstance(row, dict):
            row["score"] = after


def stance_text(ledger, faction_id, player):
    """会話に足す構えの文。中立なら空。呼び名は判定したときと同じ段のときだけ添える。"""
    faction = faction_by_id(ledger, faction_id)
    row = (ledger.get("standing") or {}).get(faction_id)
    if faction is None or not isinstance(row, dict):
        return ""
    stage = standing_stage(row.get("score", 0))
    template = STANCE_TEXTS.get(stage)
    if not template:
        return ""
    text = template.format(faction=faction["name"], player=player)
    if row.get("title") and row.get("title_stage") == stage:
        text += TITLE_TEXT.format(faction=faction["name"], player=player, title=row["title"])
    return text


# ============================================================ 勢力どうしの関係（趨勢）
def plan_trend(ledger, a, b, direction):
    """趨勢の1回の答え（a と b が近づく +1 / 離れる -1 / 0）を段の動きに起こす。

    `{"before", "after", "push", "why"}` を返す（組が無ければ None）。1回で動くのは1段まで。
    設定に書かれた関係は `SETTING_INERTIA` 回同じ向きに押されて初めて動く（逆向きに押されたら数え直す）。
    平和な世界観では `PEACEFUL_FLOOR` より下げない。
    """
    row = (ledger.get("relations") or {}).get(pair_key(a, b))
    if not isinstance(row, dict) or a == b:
        return None
    before, push = row.get("stage", 0), row.get("push", 0)
    plan = {"before": before, "after": before, "push": push, "why": "still"}
    if not direction:
        return plan
    sign = 1 if direction > 0 else -1
    floor = PEACEFUL_FLOOR if ledger.get("peaceful") else RELATION_MIN
    target = max(floor, min(RELATION_MAX, before + sign))
    if target == before:
        plan.update(push=0, why="at the limit")
        return plan
    if row.get("from_setting"):
        push = push + sign if push * sign > 0 else sign
        if abs(push) < SETTING_INERTIA:
            plan.update(push=push, why="pushed ({}/{})".format(abs(push), SETTING_INERTIA))
            return plan
    plan.update(after=target, push=0, why="moved")
    return plan


def apply_trend(ledger, a, b, plan, *, day):
    row = ledger["relations"][pair_key(a, b)]
    row["push"] = plan["push"]
    if plan["after"] != row.get("stage"):
        row["stage"] = plan["after"]
        row["updated_day"] = day


def add_event(ledger, event):
    ledger["events"].append(event)
    del ledger["events"][:-EVENT_LIMIT]


def trend_due(last_day, day, every):
    """趨勢を進める日か。最初の1回は台帳を作った日から数える。"""
    if day is None or every <= 0:
        return False
    if last_day is None:
        return False
    return day - last_day >= every
