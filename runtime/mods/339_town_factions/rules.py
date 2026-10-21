# -*- coding: utf-8 -*-
"""街の勢力の規則。ゲームには触らない（ゲーム抜きの検査で全部通せる）。

段の数と名前・見られ方の点数と段の対応・関係の動かし方・根拠の照合・控えの形を持つ。
決めた仕様と根拠は VERIFICATION.md §3.98。

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
#: 勢力どうしの仲（`RELATION_NAMES`）と同じ言葉に揃え、主人公ひとりに合わない「同盟」だけ「盟友」にした（DOC.md §1）。
STANDING_NAMES = {-2: "敵対", -1: "緊張", 0: "中立", 1: "友好", 2: "盟友"}
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
#: 見られ方が動いた来歴を台帳に持つ件数（新しいほうから）。
HISTORY_LIMIT = 40


def empty_ledger():
    # organize_asked: 設定に勢力が無い世界で、組織を設ける設計を聞いたか（聞く前の NONE は、設定を入れたら作り直す）
    # people: 勢力の街の住人の立場 `{npc_id: {town, faction, role, reason}}`（版3 から。無い台帳は判定前とみなす）
    return {"version": LEDGER_VERSION, "status": None, "why": "", "made_day": None,
            "peaceful": False, "factions": [], "towns": {}, "relations": {},
            "standing": {}, "events": [], "trend": {"last_day": None, "incidents": {}},
            "gained": {}, "history": [], "organize_asked": False, "people": {}}


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


#: 根拠の途中を省いた印。省いた根拠は切れ目ごとに照合する（2026-10-11、Haiku 5.5 が「機関:... 教会と敵対」と返した）。
_ELLIPSIS = re.compile(r"(?:\.{2,}|…+|‥+)")


def evidence_ok(evidence, material):
    """根拠の原文が材料の中に本当にあるか。空白と括弧は無視する。短すぎる根拠は認めない。

    途中を「...」「…」で省いた根拠は、切れ目ごとに材料にあれば通す。
    ただし切れ目のうち一番長いものが `EVIDENCE_MIN_CHARS` の2倍に届かなければ認めない
    （短い語をつないで原文に無い文を作れないように）。
    """
    flat = normalize(material)
    pieces = [normalize(p) for p in _ELLIPSIS.split(str(evidence or ""))]
    pieces = [p for p in pieces if p]
    if not pieces:
        return False
    if len(pieces) == 1:
        return len(pieces[0]) >= EVIDENCE_MIN_CHARS and pieces[0] in flat
    return max(len(p) for p in pieces) >= EVIDENCE_MIN_CHARS * 2 and all(p in flat for p in pieces)


#: 抽出の name_type で「一般の呼び名」を表す答え。
GENERAL_NAME = "一般"
#: 「冒険者に当たる人々」を名指しする言い方。
ADVENTURER_STAND_IN = ("冒険者の代わり", "冒険者に当たる", "冒険者にあたる")
_SENTENCE = re.compile(r"[。\n]")


def is_adventurer_stand_in(name, material):
    """設定が、その名前の人々を冒険者に当たる者か、冒険者のギルドとして書いているか。

    同じ文に名前と「冒険者の代わり」などの言い方がある、
    または同じ文に「冒険者」があって名前の直前が「ギルド」（「ギルド『〇〇』の支部」）。
    """
    key = normalize(name)
    for sentence in _SENTENCE.split(str(material or "")):
        flat = normalize(sentence)
        if not key or key not in flat:
            continue
        if any(word in flat for word in ADVENTURER_STAND_IN):
            return True
        if "冒険者" in flat and ("ギルド" + key) in flat:
            return True                 # 生成した世界の案内文で、冒険者ギルドに名前が付いた（2026-10-11 の実機）
    return False


#: 文の中で名指しを探すときの、名前の最短の字数（1字の名前は別の語の一部に紛れる）。
NAME_MIN_CHARS = 2


def sentence_naming(material, *names):
    """材料の中で、`names` をすべて含む最初の一文（原文のまま）。無ければ None。空白と括弧は無視して探す。"""
    keys = [normalize(name) for name in names]
    if not keys or any(len(key) < NAME_MIN_CHARS for key in keys):
        return None
    for sentence in _SENTENCE.split(str(material or "")):
        flat = normalize(sentence)
        if len(flat) >= EVIDENCE_MIN_CHARS and all(key in flat for key in keys):
            return sentence.strip()
    return None


# ============================================================ 台帳を組む
#: 名前の後ろの括弧書き（「霧送りの御堂（祈祷教団）」の「（祈祷教団）」）。
_TRAILING_NOTE = re.compile(r"\s*[（(][^（）()]*[)）]\s*$")


def faction_id_of(by_name, text):
    """LLM が返した勢力の名前を台帳の id に引く。引けなければ None。

    名前の後ろに種類を括弧で付けて返すことがある（頼みの一覧を写した。2026-10-11 の実機）ので、
    括弧書きを外して引き直し、それでもだめなら空白と括弧を無視して比べる。
    """
    text = str(text or "").strip()
    if text in by_name:
        return by_name[text]
    bare = _TRAILING_NOTE.sub("", text).strip()
    if bare in by_name:
        return by_name[bare]
    key = normalize(bare)
    for name, faction_id in by_name.items():
        if key and normalize(name) == key:
            return faction_id
    return None


def pair_key(a, b):
    return "|".join(sorted((str(a), str(b))))


def build_ledger(extracted, material, town_ids, *, day=None, town_names=None):
    """抽出の答えから台帳を組む。根拠が材料に無いものは捨てる。

    `extracted` は LLM の答え（辞書）。`material` は渡した材料の全文（根拠の照合に使う）。
    `town_ids` はこの世界の街の id（文字列）。`town_names` は街の id → 名前（無くてもよい）。
    返すのは `(台帳, 捨てたものの説明の並び)`。勢力が2つ未満なら台帳の status は NONE。
    """
    town_names = {str(k): str(v) for k, v in (town_names or {}).items()}
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
        if normalize(name) not in normalize(material):
            # 街の説明から組み立てた呼び名（「〇〇の支配勢力」など）を落とす（2026-10-11 の実機）
            dropped.append("faction {!r}: the name is not in the setting".format(name))
            continue
        if str(row.get("name_type") or "").strip().startswith(GENERAL_NAME):
            # 業種・職業の呼び名は勢力にしない（VERIFICATION.md §3.98「勢力は設定に書かれたものだけ」）。
            # 「数えない」と書くだけでは残ったので、勢力ごとに答えさせて落とす
            dropped.append("faction {!r}: a general name, not one organization".format(name))
            continue
        if is_adventurer_stand_in(name, material):
            # 「冒険者の代わりに〜がいる」の〜は主人公の仕事。頼み文で外させても残った（2026-10-11 の実機）
            dropped.append("faction {!r}: the setting calls them the adventurers".format(name))
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
        faction_id = faction_id_of(by_name, row.get("faction"))
        if town not in known_towns or faction_id is None:
            continue
        name = next(f["name"] for f in factions if f["id"] == faction_id)
        evidence = str(row.get("evidence") or "").strip()
        found = evidence_ok(evidence, material)
        if not found or normalize(name) not in normalize(evidence):
            # 根拠が原文に無いもの・勢力の名前の無い根拠から推して割り振ったもの
            # （Haiku 5.5 が「聖者の遺構」から教会の街とした）は捨てる。
            # ただし設定に勢力と街を1文で結ぶ文があれば、それを根拠にする。ローカルの LLM は、
            # 「〇〇と△△を治める『白翼騎士団』」と書いた世界でも、根拠に街の案内文を選ぶか言い換えて、
            # 街がほとんど落ちていた（VERIFICATION.md §3.98「勢力は設定に書かれたものだけ」）
            named = sentence_naming(material, name, town_names.get(town, ""))
            if named is None:
                dropped.append("town {}: evidence not found".format(town) if not found else
                               "town {}: the evidence does not name {!r}".format(town, name))
                continue
            evidence = named
        ledger["towns"][town] = {"faction": faction_id, "evidence": evidence}
    for a in factions:
        for b in factions:
            if a["id"] < b["id"]:
                ledger["relations"][pair_key(a["id"], b["id"])] = {
                    "stage": 0, "push": 0, "from_setting": False, "evidence": "",
                    "updated_day": day}
    name_of = {faction["id"]: faction["name"] for faction in factions}
    for row in (extracted or {}).get("relations") or []:
        if not isinstance(row, dict):
            continue
        a = faction_id_of(by_name, row.get("a"))
        b = faction_id_of(by_name, row.get("b"))
        stage = to_int(row.get("stage"), RELATION_MIN, RELATION_MAX)
        if a is None or b is None or a == b or stage is None:
            continue
        evidence = str(row.get("evidence") or "").strip()
        found = evidence_ok(evidence, material)
        flat = normalize(evidence)
        if not found or normalize(name_of[a]) not in flat or normalize(name_of[b]) not in flat:
            # 根拠は両方の勢力の名前を含む原文に限る（街の割り振りと同じ穴。材料の見出しのような
            # 無関係な原文を根拠に、設定に無い関係を入れさせない）。設定に2つの名前を1文で結ぶ文があれば、それを根拠にする
            named = sentence_naming(material, name_of[a], name_of[b])
            if named is None:
                dropped.append("relation {}-{}: evidence not found".format(a, b) if not found else
                               "relation {}-{}: the evidence does not name both".format(a, b))
                continue
            evidence = named
        if ledger["peaceful"]:
            stage = max(PEACEFUL_FLOOR, stage)
        ledger["relations"][pair_key(a, b)].update(
            stage=stage, from_setting=True, evidence=evidence)
    for faction in factions:
        ledger["standing"][faction["id"]] = {"score": 0, "title": "", "title_stage": None,
                                            "reason": ""}
    return ledger, dropped


def title_of(row):
    """判定の答えの呼び名。段階の名前（「盟友」など）をそのまま返したものは呼び名にしない（2026-10-11、ローカル）。"""
    title = str((row or {}).get("title") or "").strip()[:20]
    return "" if title in STANDING_NAMES.values() else title


def apply_initial_standing(ledger, judged):
    """初回の判定（勢力ごとの段・呼び名・理由）を点数に起こす。判定の無い勢力は中立のまま。"""
    names = {f["name"]: f["id"] for f in ledger["factions"]}
    for row in (judged or {}).get("standings") or []:
        if not isinstance(row, dict):
            continue
        faction_id = faction_id_of(names, row.get("faction"))
        stage = to_int(row.get("stage"), -2, 2)
        if faction_id is None or stage is None:
            continue
        title = title_of(row)
        ledger["standing"][faction_id] = {
            "score": STANDING_CENTER[stage], "title": title if stage else "",
            "title_stage": stage if title and stage else None,
            "reason": str(row.get("reason") or "").strip()[:200]}
    return ledger


def build_designed_ledger(designed, town_ids, *, day=None, peaceful=False):
    """設計の答え（設定に勢力が無い世界に、後から設けた組織）から台帳を組む。

    根拠の照合はしない（設定に無いものを作らせているので）。名前の重複・知らない街・
    二つの組織に属する街（先に挙げた方が勝つ）は捨てる。組織が2つ未満なら status は NONE。
    設計した関係は、設定に書かれた関係と同じく動きにくくする（世界に足した設定として扱う）。
    """
    ledger = empty_ledger()
    ledger["made_day"] = day
    ledger["peaceful"] = bool(peaceful)
    known_towns = set(str(t) for t in town_ids)
    factions, by_name = [], {}
    for row in (designed or {}).get("factions") or []:
        if not isinstance(row, dict):
            continue
        name = str(row.get("name") or "").strip()
        if not name or name in by_name or len(factions) >= 4:
            continue
        faction = {"id": "f{}".format(len(factions) + 1), "name": name,
                   "kind": str(row.get("kind") or "").strip(),
                   "note": str(row.get("note") or "").strip(), "evidence": "", "source": "designed"}
        factions.append(faction)
        by_name[name] = faction["id"]
        for town in row.get("towns") or []:
            town = str(town).strip()
            if town in known_towns and town not in ledger["towns"]:
                ledger["towns"][town] = {"faction": faction["id"], "evidence": ""}
    if len(factions) < 2:
        ledger["status"] = NONE
        ledger["why"] = "the design gave {} organization(s)".format(len(factions))
        return ledger
    ledger["status"] = READY
    ledger["factions"] = factions
    for a in factions:
        for b in factions:
            if a["id"] < b["id"]:
                ledger["relations"][pair_key(a["id"], b["id"])] = {
                    "stage": 0, "push": 0, "from_setting": True, "evidence": "", "updated_day": day}
    for row in (designed or {}).get("relations") or []:
        if not isinstance(row, dict):
            continue
        a = faction_id_of(by_name, row.get("a"))
        b = faction_id_of(by_name, row.get("b"))
        stage = to_int(row.get("stage"), RELATION_MIN, RELATION_MAX)
        if a is None or b is None or a == b or stage is None:
            continue
        if ledger["peaceful"]:
            stage = max(PEACEFUL_FLOOR, stage)
        ledger["relations"][pair_key(a, b)]["stage"] = stage
    for faction in factions:
        ledger["standing"][faction["id"]] = {"score": 0, "title": "", "title_stage": None,
                                            "reason": ""}
    return ledger


# ============================================================ 手で直した台帳
def _ranged(value, low, high):
    """範囲の整数ならそのまま、そうでなければ均した値（読めなければ 0）。"""
    if type(value) is int and low <= value <= high:
        return value
    return to_int(value, low, high) or 0


def repair(ledger):
    """台帳の中の食い違いを埋める。直したことの説明の並びを返す（直さなければ空）。

    台帳は手で直せる（DOC.md「勢力を手で直す」）。組を書き漏らすと、情勢がその組を選んだときに
    落ちていた（`apply_trend` が組を引く）。見られ方の無い勢力・関係の無い組は中立で足し、
    鍵の並びの違う組は並べ直す。知らない勢力を指す関係・街・住人は外す
    （会話の構えで勢力を引けずに落ちる）。段と点数は範囲の整数に均す。
    """
    if not isinstance(ledger, dict) or ledger.get("status") != READY:
        return []
    fixed = []
    listed = ledger.get("factions") if isinstance(ledger.get("factions"), list) else []
    factions, ids = [], []
    for faction in listed:
        faction_id = faction.get("id") if isinstance(faction, dict) else None
        if not isinstance(faction_id, str) or not faction_id or "|" in faction_id \
                or not str(faction.get("name") or "").strip():
            fixed.append("faction {!r}: dropped (no id or name)".format(faction_id))
        elif faction_id in ids:
            fixed.append("faction {}: dropped (the id is used twice)".format(faction_id))
        else:
            factions.append(faction)
            ids.append(faction_id)
    ledger["factions"] = factions
    known = set(ids)

    standing = ledger.get("standing") if isinstance(ledger.get("standing"), dict) else {}
    for faction_id in ids:
        row = standing.get(faction_id)
        if not isinstance(row, dict):
            standing[faction_id] = {"score": 0, "title": "", "title_stage": None, "reason": ""}
            fixed.append("standing {}: added as neutral".format(faction_id))
            continue
        raw = row.get("score", 0)
        score = _ranged(raw, STANDING_MIN, STANDING_MAX)
        if type(raw) is not int or score != raw:
            row["score"] = score
            fixed.append("standing {}: score set to {}".format(faction_id, score))
    ledger["standing"] = standing

    old = ledger.get("relations") if isinstance(ledger.get("relations"), dict) else {}
    relations = {}
    for key, row in old.items():
        parts = str(key).split("|")
        if len(parts) != 2 or parts[0] == parts[1] or not set(parts) <= known \
                or not isinstance(row, dict):
            fixed.append("relation {}: dropped (unknown faction)".format(key))
            continue
        pair = pair_key(*parts)
        if pair in relations:
            fixed.append("relation {}: dropped (written twice)".format(key))
            continue
        if key != pair:
            fixed.append("relation {}: renamed to {}".format(key, pair))
        raw = row.get("stage", 0)
        stage = _ranged(raw, RELATION_MIN, RELATION_MAX)
        if type(raw) is not int or stage != raw:
            row["stage"] = stage
            fixed.append("relation {}: stage set to {}".format(pair, stage))
        if type(row.get("push", 0)) is not int:
            row["push"] = 0
        relations[pair] = row
    for i, a in enumerate(ids):
        for b in ids[i + 1:]:
            pair = pair_key(a, b)
            if pair not in relations:
                relations[pair] = {"stage": 0, "push": 0, "from_setting": False, "evidence": "",
                                   "updated_day": ledger.get("made_day")}
                fixed.append("relation {}: added as neutral".format(pair))
    ledger["relations"] = relations

    towns = ledger.get("towns") if isinstance(ledger.get("towns"), dict) else {}
    for town in [t for t, row in towns.items()
                 if not isinstance(row, dict) or row.get("faction") not in known]:
        towns.pop(town)
        fixed.append("town {}: dropped (unknown faction)".format(town))
    ledger["towns"] = towns

    # 住人の立場は版3 から（無い台帳は判定前。足さない）
    people = ledger.get("people") if isinstance(ledger.get("people"), dict) else {}
    for npc_id in [n for n, row in people.items() if isinstance(row, dict)
                   and row.get("faction") and row.get("faction") not in known]:
        people.pop(npc_id)
        fixed.append("person {}: dropped (unknown faction)".format(npc_id))
    return fixed


# ============================================================ 最初の立ち位置
#: 新しいキャラとみなす滞在日数の合計（これ未満で、功績も手配も無ければ出自だけで決める）。
NEW_CHARACTER_DAYS = 90
#: 土台の点数（途中導入）。組織の街での功績1件ごと・上限・顔なじみの日数・手配。
DEED_POINTS = 10
DEED_CAP = 50
FAMILIAR_DAYS = 90
FAMILIAR_POINTS = 10
#: 手配は功績で打ち消さない（VERIFICATION.md §3.98「最初の見られ方」）。手配されていれば緊張より上へ行かず、重い手配は功績によらず敵対。
WANTED_POINTS = -40
WANTED_CEILING = -21
HEAVILY_WANTED_AT = -40
HEAVILY_WANTED_POINTS = -80


def town_records(area_history):
    """`{街の id: {"deeds": [功績の文], "days": 滞在日数, "law": 手配度 / None}}`。"""
    records = {}
    for town_id, row in (area_history or {}).items():
        if not isinstance(row, dict):
            continue
        residency = row.get("residency") if isinstance(row.get("residency"), dict) else {}
        days = residency.get("total_days")
        law = row.get("lawfulness")
        records[str(town_id)] = {
            "deeds": [str(t) for t in row.get("achievements") or [] if t],
            "days": int(days) if isinstance(days, (int, float)) and not isinstance(days, bool) else 0,
            "law": law if isinstance(law, (int, float)) and not isinstance(law, bool) else None}
    return records


def is_new_character(records):
    """新しく始めたキャラか（功績も手配も無く、滞在の合計が短い）。出自だけで最初の立ち位置を決める。"""
    deeds = sum(len(r["deeds"]) for r in records.values())
    wanted = any(r["law"] is not None and r["law"] < 0 for r in records.values())
    days = sum(r["days"] for r in records.values())
    return not deeds and not wanted and days < NEW_CHARACTER_DAYS


def baseline_scores(ledger, records):
    """途中導入の土台の点数。その勢力の街での功績・滞在・手配から規則で決める。

    `{勢力: (点数, {"deeds", "days", "law", "cap"})}`。組織の名前が記録に無くても、
    その街を治める組織ならそこで起きたことは知っている、という読み。
    `cap` は手配で決まる段階の上限（手配されていなければ None）。調整もこれより上へ動かさない。
    """
    out = {}
    for faction in ledger.get("factions") or []:
        towns = [t for t, row in (ledger.get("towns") or {}).items()
                 if row.get("faction") == faction["id"]]
        deeds = sum(len(records.get(t, {}).get("deeds") or []) for t in towns)
        days = sum(records.get(t, {}).get("days") or 0 for t in towns)
        laws = [records[t]["law"] for t in towns if t in records and records[t]["law"] is not None]
        worst = min(laws) if laws else None
        score = min(DEED_CAP, deeds * DEED_POINTS) + (FAMILIAR_POINTS if days >= FAMILIAR_DAYS else 0)
        cap = None
        if worst is not None and worst <= HEAVILY_WANTED_AT:
            score, cap = HEAVILY_WANTED_POINTS, -2
        elif worst is not None and worst < 0:
            score, cap = min(WANTED_CEILING, score + WANTED_POINTS), -1
        out[faction["id"]] = (clamp_score(score),
                              {"deeds": deeds, "days": days, "law": worst, "cap": cap})
    return out


def apply_adjusted_standing(ledger, baseline, judged):
    """土台の点数に、LLM の調整（-1 / 0 / +1 段階）を掛ける。動くのは1段階まで。

    調整が 0 なら土台の点数のまま。動いたら、動いた先の段階の真ん中の点数にする。
    手配の上限（`cap`）より上へは動かさない。
    呼び名は最後の段階が中立でなければ付ける。
    """
    names = {f["name"]: f["id"] for f in ledger["factions"]}
    rows = {}
    for row in (judged or {}).get("standings") or []:
        faction_id = faction_id_of(names, row.get("faction")) if isinstance(row, dict) else None
        if faction_id:
            rows[faction_id] = row
    for faction_id, (score, facts) in baseline.items():
        row = rows.get(faction_id) or {}
        adjust = to_int(row.get("adjust"), -1, 1) or 0
        stage = standing_stage(score)
        cap = (facts or {}).get("cap")
        final = max(-2, min(2 if cap is None else cap, stage + adjust))
        title = title_of(row)
        ledger["standing"][faction_id] = {
            "score": score if final == stage else STANDING_CENTER[final],
            "title": title if final else "", "title_stage": final if title and final else None,
            "reason": str(row.get("reason") or "").strip()[:200], "baseline": score,
            "adjust": final - stage}
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
#: 点数がこれ以下になったら、その勢力の敵対は外れない（上がっても敵対の上端で止まる。VERIFICATION.md §3.98「手配と敵対の上限」）。
ENEMY_LOCK_AT = -81
#: 敵対の上端。
ENEMY_CEILING = -61


def gain_ceiling(ledger, faction_id, laws):
    """上がる動きの上限の点数。`laws` は `{街の id: 手配度}`（いまの主人公の記録）。

    敵対が外れなくなった勢力と、その勢力の街で手配度 -40 以下なら敵対の上端。
    その勢力の街で手配されていれば緊張の上端。どちらでもなければ上限なし。
    """
    row = (ledger.get("standing") or {}).get(faction_id)
    towns = [t for t, r in (ledger.get("towns") or {}).items() if r.get("faction") == faction_id]
    found = [laws[t] for t in towns if isinstance(laws, dict) and isinstance(laws.get(t), (int, float))
             and not isinstance(laws.get(t), bool)]
    worst = min(found) if found else None
    if (isinstance(row, dict) and row.get("locked")) \
            or (worst is not None and worst <= HEAVILY_WANTED_AT):
        return ENEMY_CEILING
    if worst is not None and worst < 0:
        return WANTED_CEILING
    return STANDING_MAX


def plan_talk(ledger, faction_id, step, *, gain_per_step, loss_per_step, gained_today,
              ceiling=STANDING_MAX):
    """会話の判定（-2〜+2）を点数の動きに起こす。`[(勢力, 前, 後, 理由)]` を返す。

    上がるのは同じ勢力から1日1回（`gained_today`）。下がる方は毎回効く。
    上がったときは、その勢力と敵対・緊張している勢力から一部を引く（`RIVAL_ECHO`）。
    上がる先は `ceiling`（`gain_ceiling`）まで。
    """
    row = (ledger.get("standing") or {}).get(faction_id)
    if not isinstance(row, dict) or not step:
        return []
    before = row.get("score", 0)
    if step > 0:
        if gained_today:
            return [(faction_id, before, before, "daily")]
        return plan_gain(ledger, faction_id, step * max(0, int(gain_per_step)), ceiling=ceiling)
    after = clamp_score(before + step * max(0, int(loss_per_step)))
    return [(faction_id, before, after, "loss")]


def plan_gain(ledger, faction_id, gain, why="gain", *, ceiling=STANDING_MAX):
    """`gain` 点上げる動き。敵対・緊張している勢力からは一部を引く（`RIVAL_ECHO`）。

    上がる先は `ceiling` まで（もう上限にいれば動かさず、理由は "capped"）。
    響きは実際に上がった分から引く。
    """
    row = (ledger.get("standing") or {}).get(faction_id)
    if not isinstance(row, dict) or gain <= 0:
        return []
    before = row.get("score", 0)
    after = max(before, min(clamp_score(before + gain), ceiling))
    if after == before:
        return [(faction_id, before, before, "capped")]
    moves = [(faction_id, before, after, why)]
    for other in ledger.get("factions") or []:
        other_id = other.get("id")
        share = RIVAL_ECHO.get(relation_stage(ledger, faction_id, other_id))
        other_row = (ledger.get("standing") or {}).get(other_id)
        if other_id == faction_id or not share or not isinstance(other_row, dict):
            continue
        lost = int(round((after - before) * share))
        if lost:
            was = other_row.get("score", 0)
            moves.append((other_id, was, clamp_score(was - lost), "echo"))
    return moves


def is_locked(ledger, faction_id):
    row = (ledger.get("standing") or {}).get(faction_id)
    return isinstance(row, dict) and bool(row.get("locked"))


def lift_lock(ledger, faction_id):
    """敵対が外れない印を外す。外したら True。会話で相手を納得させたときだけ呼ぶ（VERIFICATION.md §3.98「手配と敵対の上限」）。"""
    row = (ledger.get("standing") or {}).get(faction_id)
    if not isinstance(row, dict) or not row.get("locked"):
        return False
    row.pop("locked", None)
    return True


def apply_moves(ledger, moves):
    """動きを点数に書く。`ENEMY_LOCK_AT` 以下になった勢力は敵対が外れない印を付ける（外すのは `lift_lock`）。"""
    for faction_id, _before, after, _why in moves:
        row = ledger["standing"].get(faction_id)
        if isinstance(row, dict):
            row["score"] = after
            if after <= ENEMY_LOCK_AT:
                row["locked"] = True


def stance_text(ledger, faction_id, player, role=None):
    """会話に足す構えの文。立場（`role`。既定は一員）で書き分ける。足すことが無ければ空。

    一員と他の勢力寄りは、中立なら空。呼び名は判定したときと同じ段のときだけ添える。
    """
    role = role or MEMBER
    if role == INDIFFERENT:
        return ""
    faction = faction_by_id(ledger, faction_id)
    row = (ledger.get("standing") or {}).get(faction_id)
    if faction is None or not isinstance(row, dict):
        return ""
    stage = standing_stage(row.get("score", 0))
    name = faction["name"]
    if role == FOLLOWER:
        return FOLLOWER_TEXT.format(faction=name, player=player, view=STANDING_VIEWS[stage]) if stage else ""
    if role == DISSENTER:
        text = DISSENTER_TEXT.format(faction=name)
        if stage:
            text += (DISSENTER_COLD if stage < 0 else DISSENTER_WARM).format(
                faction=name, player=player, view=STANDING_VIEWS[stage])
        return text + DISSENTER_HIDE
    template = STANCE_TEXTS.get(stage)
    if role == OTHER:
        text = OTHER_TEXT.format(faction=name)
        if template:
            text += template.format(faction=name, player=player)
        return text
    if role == OTHER_OPEN:
        text = OTHER_OPEN_TEXT.format(faction=name)
        if not template:
            return text
        text += template.format(faction=name, player=player)
    elif not template:
        return ""
    else:
        text = template.format(faction=name, player=player)
    if row.get("title") and row.get("title_stage") == stage:
        text += TITLE_TEXT.format(faction=name, player=player, title=row["title"])
    return text


# ============================================================ 住人の立場
#: 立場の英名（台帳と答えの形）。判定と根拠は VERIFICATION.md §3.98「住人の立場」。
MEMBER = "member"             # 勢力の一員
FOLLOWER = "follower"         # 一員ではないが治める側に従う普通の住人
DISSENTER = "dissenter"       # 表向き従い、内心では反感（面従腹背）
INDIFFERENT = "indifferent"   # 権威に関心が無く、損得と自分の暮らしで動く
OTHER = "other"               # 別の勢力の側にいる（台帳の `open` で公然か隠しているかを分ける）
ROLES = (MEMBER, FOLLOWER, DISSENTER, INDIFFERENT, OTHER)
#: 他の勢力寄りのうち、その勢力の者だと周りに知られている者（台帳には書かない。`role_for` が返す）。
OTHER_OPEN = "other_open"
#: 答えに日本語で書かれたときの読み替え。
ROLE_WORDS = {"一員": MEMBER, "構成員": MEMBER, "従う住人": FOLLOWER, "従う": FOLLOWER, "住人": FOLLOWER,
              "面従腹背": DISSENTER, "反感": DISSENTER, "無関心": INDIFFERENT,
              "他の勢力寄り": OTHER, "他勢力": OTHER, "別の勢力": OTHER}
#: 見られ方を会話で動かせる立場（その勢力の者として主人公を見ている）。
JUDGING_ROLES = (MEMBER, OTHER, OTHER_OPEN)
#: 段ごとの見られ方の言い方（従う住人・面従腹背の文に入れる）。
STANDING_VIEWS = {-2: "敵とみなしている", -1: "信用していない", 0: "とくに気にしていない",
                  1: "好意的に見ている", 2: "身内も同然に信頼している"}
FOLLOWER_TEXT = ("この街を治める{faction}の者たちは、{player}を{view}。"
                 "あなたは{faction}の一員ではないが、この街で暮らす者としてそれを知っており、{faction}の目を気にしている。")
DISSENTER_TEXT = "あなたは表向き{faction}に従っているが、内心では{faction}に反感を持っている。"
DISSENTER_COLD = "{faction}の者たちが{player}を{view}ことは、あなたが{player}を嫌う理由にはならない。"
DISSENTER_WARM = "{faction}の者たちが{player}を{view}ことは、あなたが{player}に好意を持つ理由にはならない。"
DISSENTER_HIDE = "人目のある所では本音を出さない。"
OTHER_TEXT = "あなたはこの街に暮らしているが、心は{faction}の側にあり、それを表には出さない。"
OTHER_OPEN_TEXT = "あなたは{faction}の者としてこの街にいて、周りもそれを知っている。"
#: 答えの `open` で「公然」と読む語。
OPEN_WORDS = ("true", "yes", "はい", "公然", "知られて")
PERSON_REASON_CHARS = 80


def person_of(ledger, npc_id):
    """台帳に書いた住人の立場 `{"town", "faction", "role", "reason"}`。判定前なら None。"""
    row = (ledger.get("people") or {}).get(str(npc_id))
    return row if isinstance(row, dict) and row.get("role") in ROLES else None


def role_for(ledger, npc_id, town_faction):
    """相手の `(勢力の id, 立場)`。判定前・名簿に無い相手は、街の勢力に従う住人。"""
    row = person_of(ledger, npc_id)
    if row is None:
        return town_faction, FOLLOWER
    if row["role"] == OTHER:
        return row.get("faction") or None, OTHER_OPEN if row.get("open") else OTHER
    if row["role"] == INDIFFERENT:
        return town_faction, INDIFFERENT
    return town_faction, row["role"]


def read_role(text):
    word = str(text or "").strip().lower()
    if word in ROLES:
        return word
    for key, role in ROLE_WORDS.items():
        if key in str(text or ""):
            return role
    return None


def read_people(answer, ledger, town_id, npc_ids):
    """住人の立場の答えを台帳の形に起こす。`({npc_id: 行}, 読めなかった理由の並び)`。

    答えに無い住人は、従う住人として書く（聞き直し続けないように）。
    """
    town_faction = faction_of_town(ledger, town_id)
    by_name = {f["name"]: f["id"] for f in ledger.get("factions") or []}
    wanted = [str(x) for x in npc_ids]
    rows, problems = {}, []
    for item in (answer or {}).get("people") or []:
        if not isinstance(item, dict):
            continue
        npc_id = str(item.get("id") or "").strip()
        if npc_id not in wanted:
            problems.append("unknown id {!r}".format(item.get("id")))
            continue
        role = read_role(item.get("role"))
        faction = faction_id_of(by_name, item.get("faction")) if item.get("faction") else None
        if role is None:
            problems.append("{}: unreadable role {!r}".format(npc_id, item.get("role")))
            role = FOLLOWER
        said_open = str(item.get("open") or "").strip().lower()
        is_open = any(word in said_open for word in OPEN_WORDS)
        if faction and faction != town_faction and role != OTHER:
            # 別の勢力との結び付きを答えた（役の選び方より勢力の欄を信じる。役が割れた。2026-10-14、Haiku 5.5）。
            # 一員と答えた者は、隠していると答えなければ公然
            is_open = is_open or (role == MEMBER and said_open in ("", "none", "null"))
            role = OTHER
        if role == OTHER and (not faction or faction == town_faction):
            problems.append("{}: other without a faction {!r}".format(npc_id, item.get("faction")))
            role, faction = FOLLOWER, None
        rows[npc_id] = {"town": str(town_id), "faction": faction if role == OTHER else town_faction,
                        "role": role,
                        "reason": str(item.get("reason") or "").strip()[:PERSON_REASON_CHARS]}
        if role == OTHER:
            rows[npc_id]["open"] = is_open
    for npc_id in wanted:
        if npc_id not in rows:
            rows[npc_id] = {"town": str(town_id), "faction": town_faction, "role": FOLLOWER,
                            "reason": "", "missing": True}
    return rows, problems


def unjudged(ledger, residents):
    """まだ立場を決めていない住人 `{town_id: [npc_id]}`。`residents` は `{town_id: [npc_id]}`。勢力の街だけ。

    別の街で決めた住人（住まいが変わった）も、いまの街で決め直す。
    """
    people = ledger.get("people") or {}
    out = {}
    for town_id, ids in residents.items():
        if not faction_of_town(ledger, town_id):
            continue
        rest = [str(x) for x in ids if not isinstance(people.get(str(x)), dict)
                or str(people[str(x)].get("town")) != str(town_id)]
        if rest:
            out[str(town_id)] = rest
    return out


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


# ============================================================ 見られ方の来歴
#: 来歴の種類。
TALK, INCIDENT, ECHO, RECONCILE = "talk", "incident", "echo", "reconcile"
#: 勢力の刺客を討った回（`incident` に刺客の呼び名）。
HUNTER = "hunter"


def add_history(ledger, entry):
    """来歴を1件足す（古いものから捨てる）。前の版の台帳には `history` が無いので作る。"""
    history = ledger.setdefault("history", [])
    history.append(entry)
    del history[:-HISTORY_LIMIT]


def record_moves(ledger, moves, *, day, kind, reason="", incident=""):
    """点数の動き（`plan_talk` / `plan_gain` の答え）を来歴に書く。動かなかったもの（上限・1日1回）は書かない。

    響き（`echo`）は、上がった勢力（動きの先頭）を `by` に持つ。
    書いた件数を返す（段階が変わったものがあれば `changed` も真）。
    """
    source = moves[0][0] if moves else None
    written, changed = 0, False
    for faction_id, before, after, why in moves:
        if after == before or why in ("daily", "capped"):
            continue
        echo = why == "echo"
        add_history(ledger, {"day": day, "faction": faction_id, "before": before, "after": after,
                             "kind": ECHO if echo else kind, "by": source if echo else None,
                             "reason": "" if echo else str(reason or "")[:200],
                             "incident": "" if echo else str(incident or "")[:60]})
        written += 1
        changed = changed or standing_stage(before) != standing_stage(after)
    return written, changed


def _day(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def needs_rewind(ledger, day):
    """暦が台帳より前にあるか（保存の前に落ちて古いセーブを読んだ・同じ名前で作り直した）。"""
    if day is None or not isinstance(ledger, dict):
        return False
    days = [_day(ledger.get("made_day")), _day((ledger.get("trend") or {}).get("last_day"))]
    days += [_day(row.get("day")) for row in ledger.get("events") or [] if isinstance(row, dict)]
    days += [_day(row.get("day")) for row in ledger.get("history") or [] if isinstance(row, dict)]
    return any(d is not None and d > day for d in days)


def rewind(ledger, day):
    """その日より後に起きたことを台帳から外す。`"rebuild"`（台帳がその日より後に作られた）/ 真（外した）/ 偽。

    見られ方は来歴を新しいほうから戻し（`before` へ）、勢力どうしの関係は情勢の出来事を戻す。
    設定由来の関係の押し（`push`）は戻さない（来歴に残っていない）。
    """
    if not needs_rewind(ledger, day):
        return False
    made = _day(ledger.get("made_day"))
    if made is not None and made > day:
        return "rebuild"
    standing = ledger.get("standing") or {}
    history = [row for row in ledger.get("history") or [] if isinstance(row, dict)]
    keep = []
    for row in reversed(history):
        when = _day(row.get("day"))
        if when is None or when <= day:
            keep.append(row)
            continue
        state = standing.get(row.get("faction"))
        if not isinstance(state, dict):
            continue
        if row.get("kind") == RECONCILE:
            state["locked"] = True          # 外した印を戻す
        before = row.get("before")
        if isinstance(before, (int, float)):
            if state.get("score", 0) <= ENEMY_LOCK_AT < before:
                state.pop("locked", None)   # この動きで付いた印
            state["score"] = clamp_score(before)
    ledger["history"] = list(reversed(keep))
    relations = ledger.get("relations") or {}
    events = [row for row in ledger.get("events") or [] if isinstance(row, dict)]
    keep = []
    for row in reversed(events):
        when = _day(row.get("day"))
        if when is None or when <= day:
            keep.append(row)
            continue
        pair = relations.get(pair_key(row.get("a"), row.get("b")))
        if isinstance(pair, dict) and isinstance(row.get("before"), int):
            pair["stage"] = row["before"]
    ledger["events"] = list(reversed(keep))
    trend = ledger.setdefault("trend", {"last_day": None, "incidents": {}})
    if _day(trend.get("last_day")) is not None and trend["last_day"] > day:
        trend["last_day"] = day
    gained = ledger.get("gained") or {}
    for faction_id in [f for f, when in gained.items() if _day(when) is not None and when > day]:
        gained.pop(faction_id, None)
    return True


def trend_due(last_day, day, every, extra=0):
    """趨勢を進める日か。最初の1回は台帳を作った日から数える。

    間隔は `every` に `extra`（情勢が動くたびに引き直すばらつき）を足した日数。`every` が 0 なら進めない。
    """
    if day is None or every <= 0:
        return False
    if last_day is None:
        return False
    return day - last_day >= every + max(0, extra)


# ============================================================ 刺客
#: 刺客の控え（台帳の `hunters`。`{npc_id: {faction, name, title, town, status, day}}`）の状態。
HUNTER_READY = "ready"          # 作った。まだ呼び止めていない
HUNTER_MET = "met"              # 呼び止めた（会話の中）
HUNTER_FIGHTING = "fighting"    # 戦っている
HUNTER_ESCAPED = "escaped"      # 主人公が戦闘から逃げた。生きていて、また現れる
HUNTER_DEAD = "dead"            # 主人公が討った
#: まだ主人公の前に現れうる状態。
HUNTER_ALIVE = (HUNTER_READY, HUNTER_MET, HUNTER_FIGHTING, HUNTER_ESCAPED)


def hunter_rate(stage, hostile_rate, wary_rate):
    """その段階の勢力の街で刺客が現れる確率（％）。敵対と緊張のほかは 0。"""
    rate = hostile_rate if stage <= -2 else wary_rate if stage == -1 else 0
    try:
        return max(0, min(100, int(rate)))
    except (TypeError, ValueError):
        return 0


def hunters_of(ledger):
    """台帳の刺客の控え。無ければ作って返す（版5 から）。"""
    found = ledger.get("hunters")
    if not isinstance(found, dict):
        found = ledger["hunters"] = {}
    return found


def hunter_of(ledger, npc_id):
    row = (ledger.get("hunters") or {}).get(str(npc_id)) if isinstance(ledger, dict) else None
    return row if isinstance(row, dict) else None


def waiting_hunter(ledger, faction_id, alive):
    """その勢力のまだ現れうる刺客の id（いちばん古いもの）。`alive(npc_id)` が偽の刺客は数えない。"""
    rows = [(row.get("day") or 0, npc_id) for npc_id, row in (ledger.get("hunters") or {}).items()
            if isinstance(row, dict) and row.get("faction") == faction_id
            and row.get("status") in HUNTER_ALIVE and alive(npc_id)]
    return min(rows)[1] if rows else None


def hunter_due(last_day, day, gap):
    """前の刺客から `gap` 日たったか。まだ現れたことが無ければ真。"""
    if day is None:
        return False
    last = _day(last_day)
    return last is None or day - last >= max(0, gap)


def hunter_loss(ledger, faction_id, loss):
    """刺客を討ったときの見られ方の動き（`apply_moves` / `record_moves` の形）。勢力が無ければ空。"""
    row = (ledger.get("standing") or {}).get(faction_id)
    if not isinstance(row, dict) or loss <= 0:
        return []
    before = row.get("score", 0)
    return [(faction_id, before, clamp_score(before - int(loss)), HUNTER)]


def wanted_after(before, current, loss):
    """刺客を討った後の手配度。ゲームが先に下げていれば重ねない（前から `loss` 下がった所まで）。"""
    if before is None or loss <= 0:
        return current
    target = int(before) - int(loss)
    return target if current is None or current > target else current


def wanted_after_escape(before, current):
    """刺客から逃げた後の手配度。ゲームが下げた分を戦闘の前へ戻す（上がっていればそのまま）。"""
    if before is None or current is None:
        return current
    return max(int(before), int(current))
