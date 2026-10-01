# -*- coding: utf-8 -*-
"""ライバルの台帳と判定。ゲームには触らない（`tools/tests/test_wip_rival_adventurer.py` が直に呼ぶ）。

台帳は `state\\rival_adventurer\\<世界×主人公>.json` に1つ:

    {"rival":  {"id", "name", "chosen_day", "stance", "stance_told",
                "reason", "intro", "intro_told"} か None,
     "target": {"quest", "area", "title", "set_day", "due_day", "told"} か None,
     "taken":  {依頼id: {"area", "title", "day", "told", "by"}},   by は片付けたライバルの名前
     "failure": {"quest", "area", "title", "day", "told"} か None,
     "score":  {"rival", "player", "failed"},
     "history": [{"title", "outcome", "day"}],   最近の取り合い（新しい順）
     "topics":  [{"kind", "title", "day"}],      ギルドで声をかけるときの話の種（新しい順）
     "away_until": 日 か None,     しくじって休んでいる間
     "next_day":   日 か None}     次に狙いを付けてよい日

設計の経緯は DOC.md。
"""

#: 台帳の鍵の並び（`state\` の差分を読めるように固定する）。
BUCKET_KEYS = ("rival", "target", "taken", "failure", "score", "history", "topics",
               "away_until", "next_day")
SCORE_KEYS = ("rival", "player", "failed")
RIVAL_KEYS = ("id", "name", "chosen_day", "stance", "stance_told", "reason",
              "intro", "intro_told")

#: ライバルの態度の段。0 見下す / 1 一目置く / 2 認める / 3 慕う。下がらない。
STANCE_TOP = 3

#: 取り合いの結末（`history` の `outcome`）。
RIVAL_WON, PLAYER_WON, RIVAL_FAILED = "rival", "player", "failed"
#: ライバルがしくじった依頼をプレイヤーが片付けた（尻拭い）。
PLAYER_CLEANED = "cleanup"

#: 声をかけるときの話の種（`topics` の `kind`）。初対面は他より先に使う。
TOPIC_INTRO, TOPIC_TAKEN, TOPIC_LOST, TOPIC_CLEANUP = "intro", "taken", "lost", "cleanup"


def new_bucket():
    return {"rival": None, "target": None, "taken": {}, "failure": None,
            "score": {key: 0 for key in SCORE_KEYS}, "history": [], "topics": [],
            "away_until": None, "next_day": None}


def order_bucket(bucket):
    """書く直前に並びを固定する。欠けた鍵は既定で埋める。"""
    fresh = new_bucket()
    out = {}
    for key in BUCKET_KEYS:
        value = bucket.get(key, fresh[key]) if isinstance(bucket, dict) else fresh[key]
        if key == "taken":
            value = value if isinstance(value, dict) else {}
            value = {qid: value[qid] for qid in sorted(value, key=_id_key)}
        elif key == "score":
            value = value if isinstance(value, dict) else {}
            value = {name: _int(value.get(name), 0) for name in SCORE_KEYS}
        elif key == "rival" and isinstance(value, dict):
            value = dict([(name, value.get(name)) for name in RIVAL_KEYS] +
                         [(name, value[name]) for name in value if name not in RIVAL_KEYS])
        elif key in ("history", "topics"):
            value = [row for row in value if isinstance(row, dict)] \
                if isinstance(value, list) else []
        out[key] = value
    return out


def reset_rival(bucket):
    """ライバルが居なくなった・選び直した。片付けた依頼（`taken`）だけ残して台帳を空にする。

    片付けた依頼は世界の中ではもう片付いているので、掲示板へ戻さない（本人の判断）。
    勝敗・態度・取り合いの記録・話の種は次のライバルのために空にする。
    """
    taken = taken_of(bucket)
    bucket.update(new_bucket())
    bucket["taken"] = taken


def score_of(bucket):
    score = bucket.get("score")
    if not isinstance(score, dict):
        score = {key: 0 for key in SCORE_KEYS}
        bucket["score"] = score
    for key in SCORE_KEYS:
        score[key] = _int(score.get(key), 0)
    return score


def taken_of(bucket):
    taken = bucket.get("taken")
    if not isinstance(taken, dict):
        taken = {}
        bucket["taken"] = taken
    return taken


def history_of(bucket):
    history = bucket.get("history")
    if not isinstance(history, list):
        history = []
        bucket["history"] = history
    return history


def push_history(bucket, title, outcome, day, limit):
    """取り合いの結末を1件、先頭に積む。`limit` 件より古いものは落とす。"""
    history = history_of(bucket)
    history.insert(0, {"title": title, "outcome": outcome, "day": day})
    del history[max(0, int(limit)):]


def topics_of(bucket):
    topics = bucket.get("topics")
    if not isinstance(topics, list):
        topics = []
        bucket["topics"] = topics
    return topics


def push_topic(bucket, kind, title, day, limit):
    """話の種を1つ積む。同じ種類は新しい方だけ残す。`limit` 件より古いものは落とす。"""
    topics = [row for row in topics_of(bucket) if row.get("kind") != kind]
    topics.insert(0, {"kind": kind, "title": title, "day": day})
    bucket["topics"] = topics[:max(0, int(limit))]


def next_topic(bucket):
    """次に使う話の種。初対面があればそれ、無ければいちばん新しいもの。無ければ None。"""
    topics = topics_of(bucket)
    for row in topics:
        if row.get("kind") == TOPIC_INTRO:
            return row
    return topics[0] if topics else None


def drop_topic(bucket, topic):
    """使った話の種を下げる。"""
    bucket["topics"] = [row for row in topics_of(bucket) if row is not topic]


def drop_topic_like(bucket, topic):
    """同じ中身（種類・題名・日）の種を下げる。

    会話を始めた後に下げるときは、控えを読み直しているので同じ物（`is`）とは限らない。
    """
    if not isinstance(topic, dict):
        return
    sign = (topic.get("kind"), topic.get("title"), topic.get("day"))
    bucket["topics"] = [row for row in topics_of(bucket)
                        if (row.get("kind"), row.get("title"), row.get("day")) != sign]


# ---------------------------------------------------------------- 態度
def stance_of(bucket):
    rival = bucket.get("rival")
    value = _int(rival.get("stance"), 0) if isinstance(rival, dict) else 0
    return max(0, min(STANCE_TOP, value))


def stance_from_wins(player_wins, wins_per_step):
    """先に片付けた回数から見た段。`wins_per_step` が 0 なら上げない。"""
    if wins_per_step <= 0:
        return 0
    return max(0, min(STANCE_TOP, int(player_wins) // int(wins_per_step)))


def stance_from_affinity(affinity, per_step):
    """ライバルの好感度から見た段。`per_step` が 0 か好感度が読めなければ 0。"""
    if per_step <= 0 or not isinstance(affinity, (int, float)) or isinstance(affinity, bool):
        return 0
    return max(0, min(STANCE_TOP, int(affinity) // int(per_step)))


def raise_stance(bucket, floor):
    """段を `floor` まで上げる（下げない）。上がったら新しい段、変わらなければ None。"""
    rival = bucket.get("rival")
    if not isinstance(rival, dict):
        return None
    now = stance_of(bucket)
    floor = max(0, min(STANCE_TOP, int(floor)))
    if floor <= now:
        return None
    rival["stance"] = floor
    rival["stance_told"] = False
    return floor


# ---------------------------------------------------------------- 選ぶ
def may_draw(clears, after):
    """ライバルの抽選をしてよいか（片付けた依頼が `after` 件以上）。"""
    return isinstance(clears, int) and clears >= max(0, int(after))


def pick_rival(candidates, player_level, rng):
    """`[(npc_id, level)]` から、プレイヤーの Lv にいちばん近い1人の id。

    同じ近さが並んだら乱数で選ぶ。Lv が読めない人は選ばない。候補が無ければ None。
    """
    rows = [(str(npc_id), level) for npc_id, level in candidates
            if isinstance(level, int) and not isinstance(level, bool)]
    if not rows:
        return None
    if not isinstance(player_level, int) or isinstance(player_level, bool):
        player_level = 0
    best = min(abs(level - player_level) for _npc_id, level in rows)
    nearest = sorted((npc_id for npc_id, level in rows
                      if abs(level - player_level) == best), key=_id_key)
    return nearest[rng.randrange(len(nearest))]


def pick_target(quests, level, reach, rng):
    """`[(quest_id, difficulty)]` から狙う1件の id。

    手の届く依頼（難易度 <= Lv + `reach`）だけを候補にし、その中から乱数で選ぶ。
    手の届く依頼が無ければ None（無理な依頼には目を付けない）。
    """
    if not isinstance(level, int) or isinstance(level, bool):
        return None
    limit = level + max(0, int(reach))
    rows = sorted((str(qid) for qid, difficulty in quests
                   if isinstance(difficulty, int) and not isinstance(difficulty, bool)
                   and difficulty <= limit), key=_id_key)
    if not rows:
        return None
    return rows[rng.randrange(len(rows))]


def due_day(day, rng, low, high):
    """狙いの期限の日。`low`〜`high` 日後（逆さに入れられても均す）。"""
    low, high = max(1, int(low)), max(1, int(high))
    if high < low:
        low, high = high, low
    return int(day) + rng.randint(low, high)


# ---------------------------------------------------------------- 決着
def success_chance(level, difficulty, base_percent, slope_percent,
                   low_percent=10, high_percent=95):
    """ライバルが依頼を片付ける確率（0..1）。

    Lv と難易度が同じとき `base_percent`。Lv が1上回るごとに `slope_percent` ずつ上がる。
    敵の Lv は難易度 + 1 なので（GAME.md §2.10）、両者は同じ物差しで比べられる。
    """
    if not isinstance(level, int) or isinstance(level, bool):
        level = 0
    if not isinstance(difficulty, int) or isinstance(difficulty, bool):
        difficulty = level
    percent = base_percent + (level - difficulty) * slope_percent
    percent = max(low_percent, min(high_percent, percent))
    return percent / 100.0


def is_due(target, day):
    if not isinstance(target, dict) or day is None:
        return False
    return int(day) >= _int(target.get("due_day"), 0)


def days_left(target, day):
    if not isinstance(target, dict) or day is None:
        return 0
    return max(0, _int(target.get("due_day"), 0) - int(day))


def is_away(bucket, day):
    until = bucket.get("away_until")
    return isinstance(until, int) and day is not None and int(day) < until


def may_aim(bucket, day):
    """次の狙いを付けてよいか（狙い中・休み中・間を置いている間は付けない）。"""
    if bucket.get("target") or day is None or is_away(bucket, day):
        return False
    next_day = bucket.get("next_day")
    return not isinstance(next_day, int) or int(day) >= next_day


# ---------------------------------------------------------------- 噂
def rumors_in(bucket, area_id, day, within):
    """その土地でライバルが片付けた依頼のうち、`within` 日以内のもの。新しい順。"""
    rows = []
    for quest_id, row in taken_of(bucket).items():
        if not isinstance(row, dict) or str(row.get("area")) != str(area_id):
            continue
        when = _int(row.get("day"), None)
        if when is None or day is None:
            continue
        ago = int(day) - when
        if 0 <= ago <= max(0, int(within)):
            rows.append((ago, str(quest_id), row))
    rows.sort(key=lambda item: (item[0], _id_key(item[1])))
    return [(quest_id, row, ago) for ago, quest_id, row in rows]


def untold_in(bucket, area_id):
    """その土地でまだ知らせていない出来事 `[(種類, 依頼id, 行)]`。種類は taken / failure。"""
    found = []
    for quest_id in sorted(taken_of(bucket), key=_id_key):
        row = taken_of(bucket)[quest_id]
        if isinstance(row, dict) and not row.get("told") \
                and str(row.get("area")) == str(area_id):
            found.append(("taken", quest_id, row))
    failure = bucket.get("failure")
    if isinstance(failure, dict) and not failure.get("told") \
            and str(failure.get("area")) == str(area_id):
        found.append(("failure", str(failure.get("quest")), failure))
    return found


# ---------------------------------------------------------------- 古いセーブ
def rewind(bucket, day):
    """セーブの日付より後の出来事を忘れる。何か変えたら True。

    ゲームは行動のたびに上書き保存するので、ここに当たるのは
    台帳を書いてから次の保存までに落ちたときだけ。
    """
    if day is None:
        return False
    changed = False
    taken = taken_of(bucket)
    for quest_id in list(taken):
        row = taken[quest_id]
        if not isinstance(row, dict) or _int(row.get("day"), 0) > day:
            taken.pop(quest_id, None)
            changed = True
    target = bucket.get("target")
    if isinstance(target, dict) and _int(target.get("set_day"), 0) > day:
        bucket["target"] = None
        changed = True
    failure = bucket.get("failure")
    if isinstance(failure, dict) and _int(failure.get("day"), 0) > day:
        bucket["failure"] = None
        bucket["away_until"] = None
        changed = True
    topics = topics_of(bucket)
    kept_topics = [row for row in topics if _int(row.get("day"), 0) <= day]
    if len(kept_topics) != len(topics):
        bucket["topics"] = kept_topics
        changed = True
    history = history_of(bucket)
    kept = [row for row in history if _int(row.get("day"), 0) <= day]
    if len(kept) != len(history):
        bucket["history"] = kept
        changed = True
    next_day = bucket.get("next_day")
    if changed and isinstance(next_day, int) and next_day > day:
        # 次に狙ってよい日は、忘れた決着（片付けた・しくじった・先を越された）が決めたもの。
        # 残すと、しくじりを忘れた後もライバルが休み続ける。
        bucket["next_day"] = None
    rival = bucket.get("rival")
    if isinstance(rival, dict) and _int(rival.get("chosen_day"), 0) > day:
        bucket.update(new_bucket())
        changed = True
    return changed


# ---------------------------------------------------------------- 文型
def format_text(template, **fields):
    """設定の文型に値を入れる。鍵が足りなくても落とさない（空にする）。"""
    if not isinstance(template, str) or not template.strip():
        return ""

    class _Missing(dict):
        def __missing__(self, key):
            return ""

    try:
        return template.format_map(_Missing(fields)).strip()
    except (ValueError, IndexError):
        return ""


def _int(value, fallback):
    if isinstance(value, bool):
        return fallback
    if isinstance(value, int):
        return value
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return fallback


def _id_key(value):
    try:
        return (0, int(value))
    except (TypeError, ValueError):
        return (1, str(value))
