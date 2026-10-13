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
              "ended", "end_by", "end_by_name", "quest", "goods", "tries", "text", "told", "good", "stage_name")
#: `summary` は版1 の1文（段に依らない）。版2 からは段ごとの `summary_<段>` を書かせる
TEXT_KEYS = ("name", "summary", "summary_1", "summary_2", "summary_3", "summary_4",
             "arrive_1", "arrive_2", "arrive_3", "arrive_4", "shop", "settled", "quest", "rumor")

ALL = None      # 効き目の品の種類で「全品」
GOODS = "goods"  # 効き目の品の種類で「発生のときに選んだ1種」

#: 出来事の表の置き場所。同梱は `incidents.default.json`（同じフォルダに `incidents.json` があればそちらを読む。
#: `120_` の名簿・`132_` の種と同じ）、足す出来事は `kinds\` に1ファイル1出来事。
#: 書き方は DOC.md「出来事を足す」、読み方の決まりは VERIFICATION.md §3.97「出来事の表」。
DEFAULT_FILE = "incidents.default.json"
USER_FILE = "incidents.json"
EXTRA_DIRNAME = "kinds"
SIZES = ("village", "town", "city")
TONES = ("good", "bad", "calm")
GOODS_KEYS = ("all", "random")

# 1つの出来事（ファイルを読んだ後の形。窓・段・値段の処理はこれだけを見る）:
#   key       控えに書く英名（世界観に依らない。取り決め）
#   name      抽象の呼び名（LLM が書けなかったとき・窓の既定）
#   what      LLM に渡す説明（何が起きたか）
#   examples  世界ごとの例（LLM に渡す）
#   size      起こりやすさが 3 になる規模（village/town/city の並び）。"any" はどの規模でも 2
#   weight    起こりやすさの倍率
#   quest     解決の依頼の中身（LLM が書けなかったとき）。None なら依頼を作らない（良い出来事）
#   stages    段の名前の並び。into はその段へ移るときの言い方、tones は窓の色の調子
#   effects   値段の効き目 [(品の種類の並び or ALL or GOODS, "buy"/"sell", (段ごとの %))]
#   inn       宿代の効き目（段ごとの %）か None
#   arrive    街に着いたときの1行（段ごと。LLM が書けなかったときの文）
#   end / ended / shop / settled / rumor   収まるまでの言い方・終わった欄の言い方・LLM が書けなかったときの文


def _text(value):
    return value.strip() if isinstance(value, str) else ""


def _number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(round(value))


def parse_kind(data, where=""):
    """ファイルの1件を読む。`(出来事 or None, 読めなかった理由の並び)`。

    `{"key": …, "enabled": false}` だけのものは「止める」印として `(None, [])` ではなく
    `({"key": …, "enabled": False}, [])` を返す（同梱の出来事を止めるのに使う）。
    """
    problems = []
    if not isinstance(data, dict):
        return None, ["{}: 出来事は {{…}} で書く".format(where)]
    key = _text(data.get("key"))
    if not key or not all(ch.isascii() and (ch.isalnum() or ch == "_") for ch in key):
        return None, ["{}: key は英数字と _ で書く（{!r}）".format(where, data.get("key"))]
    where = "{} {}".format(where, key).strip()
    enabled = data.get("enabled", True) is not False
    if not enabled and set(data) <= {"key", "enabled"}:
        return {"key": key, "enabled": False}, []
    for field in ("name", "what"):
        if not _text(data.get(field)):
            problems.append("{}: {} が無い".format(where, field))
    size = data.get("size", "any")
    if size != "any":
        size = [s for s in (size if isinstance(size, list) else [size]) if s in SIZES]
        if not size:
            problems.append("{}: size は any か village / town / city の並び".format(where))
    weight = data.get("weight", 1)
    if isinstance(weight, bool) or not isinstance(weight, (int, float)) or weight < 0:
        problems.append("{}: weight は 0 以上の数".format(where))
        weight = 1
    stages = data.get("stages")
    if not isinstance(stages, list) or not stages:
        problems.append("{}: stages に段を1つ以上書く".format(where))
        stages = []
    count = len(stages)
    names, into, tones, arrive, inn = [], [], [], [], []
    effects = {}
    for index, stage in enumerate(stages):
        at = "{} の {}段目".format(where, index + 1)
        if not isinstance(stage, dict):
            problems.append("{}: 段は {{…}} で書く".format(at))
            continue
        names.append(_text(stage.get("name")))
        if not names[-1]:
            problems.append("{}: name が無い".format(at))
        tone = stage.get("tone")
        if tone not in TONES:
            problems.append("{}: tone は good / bad / calm".format(at))
        tones.append(tone if tone in TONES else "bad")
        verb = _text(stage.get("into"))
        if index and not verb:
            problems.append("{}: into（この段へ移るときの言い方）が無い".format(at))
        into.append(verb or None)
        arrive.append(_text(stage.get("arrive")))
        if not arrive[-1]:
            problems.append("{}: arrive が無い".format(at))
        rate = _number(stage.get("inn", 0))
        inn.append(rate if rate is not None else 0)
        for entry in stage.get("prices") or []:
            goods = entry.get("goods") if isinstance(entry, dict) else None
            if goods == "all":
                target = ALL
            elif goods == "random":
                target = GOODS
            elif isinstance(goods, list) and goods and all(g in CATEGORIES for g in goods):
                target = tuple(goods)
            else:
                problems.append("{}: prices の goods は all / random / 品の種類の並び（{}）".format(
                    at, "・".join(CATEGORIES)))
                continue
            for side in ("buy", "sell"):
                if side not in entry:
                    continue
                rate = _number(entry.get(side))
                if rate is None:
                    problems.append("{}: prices の {} は数".format(at, side))
                    continue
                rates = effects.setdefault((target, side), [0] * count)
                rates[index] += rate
    if problems:
        return None, problems
    quest = data.get("quest")
    kind = {"key": key, "enabled": enabled, "name": _text(data.get("name")), "what": _text(data.get("what")),
            "examples": _text(data.get("examples")), "size": size if size == "any" else tuple(size),
            "weight": float(weight), "quest": _text(quest) or None,
            "stages": tuple(names), "into": tuple(into), "tones": tuple(tones),
            "effects": [(target, side, tuple(rates)) for (target, side), rates in effects.items()],
            "inn": tuple(inn) if any(inn) else None, "arrive": tuple(arrive),
            "end": _text(data.get("end")) or "収まる", "ended": _text(data.get("ended")) or "自然に収まった",
            "shop": _text(data.get("shop")), "settled": _text(data.get("settled")), "rumor": _text(data.get("rumor"))}
    return kind, []


def _read_json(path):
    import json
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def load_kinds(default_path, extra_dir=None):
    """同梱の表と、足した出来事のフォルダを読む。`(出来事の並び, 読めなかった理由の並び)`。

    足した出来事は同じ key の同梱の出来事を差し替え、`enabled: false` だけのものは止める。
    読めなかった出来事だけを飛ばし、ほかはふだんどおり使う。
    """
    import os
    problems = []
    kinds = {}
    try:
        rows = _read_json(default_path)
    except (OSError, ValueError) as exc:
        rows = []
        problems.append("{}: 読めない（{}）".format(os.path.basename(default_path), exc))
    for index, row in enumerate(rows if isinstance(rows, list) else []):
        kind, found = parse_kind(row, "{} の{}件目".format(os.path.basename(default_path), index + 1))
        problems += found
        if kind is not None:
            kinds[kind["key"]] = kind
    names = []
    if extra_dir and os.path.isdir(extra_dir):
        names = sorted(name for name in os.listdir(extra_dir) if name.lower().endswith(".json"))
    for name in names:
        try:
            data = _read_json(os.path.join(extra_dir, name))
        except (OSError, ValueError) as exc:
            problems.append("kinds\\{}: 読めない（{}）".format(name, exc))
            continue
        kind, found = parse_kind(data, "kinds\\{}".format(name))
        problems += found
        if kind is None:
            continue
        if "stages" not in kind:                    # 止める印だけ
            if kind["key"] in kinds:
                kinds[kind["key"]] = dict(kinds[kind["key"]], enabled=False)
            continue
        kinds[kind["key"]] = kind
    return [kind for kind in kinds.values() if kind.get("enabled", True)], problems


KINDS = []
KIND_BY_KEY = {}
PROBLEMS = []


def use_kinds(kinds, problems=()):
    """読んだ表を使う（同じリスト・辞書を書き換えるので、先に import した側にも効く）。"""
    KINDS[:] = list(kinds)
    KIND_BY_KEY.clear()
    KIND_BY_KEY.update((kind["key"], kind) for kind in KINDS)
    PROBLEMS[:] = list(problems)


def reload_kinds(mod_dir):
    """MOD のフォルダの表（手元の `incidents.json` か同梱の既定）と `kinds\\` を読み直して使う。
    読めなかった理由の並びを返す。"""
    import os
    table = os.path.join(mod_dir, USER_FILE)
    if not os.path.isfile(table):
        table = os.path.join(mod_dir, DEFAULT_FILE)
    kinds, problems = load_kinds(table, os.path.join(mod_dir, EXTRA_DIRNAME))
    use_kinds(kinds, problems)
    return problems


def kind_of(event_or_kind):
    if isinstance(event_or_kind, dict) and "stages" in event_or_kind:
        return event_or_kind
    if isinstance(event_or_kind, dict):
        return KIND_BY_KEY.get(event_or_kind.get("kind"))
    return None


def last_stage(event_or_kind):
    kind = kind_of(event_or_kind)
    return len(kind["stages"]) if kind else STAGES[-1]


def stage_name(event):
    """いまの段の名前（「兆し」「盛況」など）。表に無い出来事は控えに書いた名前。"""
    kind = kind_of(event)
    if kind is None:
        return event.get("stage_name") or STAGE_NAMES.get(_int(event.get("stage"), 1), "")
    stage = min(max(_int(event.get("stage"), 1), 1), len(kind["stages"]))
    return kind["stages"][stage - 1]


def is_good(event_or_kind):
    """良い出来事か（解決の依頼を作らない）。"""
    kind = kind_of(event_or_kind)
    if kind is None:
        return bool(event_or_kind.get("good")) if isinstance(event_or_kind, dict) else False
    return kind.get("quest") is None


def stage_tones(event_or_kind):
    """段ごとの窓の色の調子（`good1`・`bad3`・`calm` など）。同じ調子が続く段は、後ろほど濃い番号。

    困り事の3段は bad1・bad2・bad3（黄・橙・赤）、良い出来事は good1・good2 の後に calm。
    1つだけの bad は一番重い色（バブルの崩壊）。
    """
    kind = kind_of(event_or_kind)
    tones = kind["tones"] if kind else ("bad",) * STAGES[-1]
    out = []
    for tone in tones:
        if tone == "calm":
            out.append("calm")
            continue
        total = tones.count(tone)
        rank = sum(1 for prev in out if prev.startswith(tone)) + 1
        out.append("{}{}".format(tone, 3 - total + rank if tone == "bad" else min(rank, 2)))
    return tuple(out)


def mark_stage(event):
    """控えに、良い出来事かといまの段の名前を書く（`339_` が段の名前を決め打ちせずに読むため）。"""
    event["good"] = is_good(event)
    event["stage_name"] = stage_name(event)


reload_kinds(__import__("os").path.dirname(__import__("os").path.abspath(__file__)))


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
            weight = 1
        elif kind["size"] == "any":
            weight = 2
        elif size in kind["size"]:
            weight = 3
        else:
            weight = 1
        out.append((kind, weight * kind.get("weight", 1)))
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
    mark_stage(event)
    bucket.setdefault("events", []).append(event)
    return event


def change_kind(event, kind, rng):
    """世界に合わないと言われた出来事を、別の種類にする。"""
    event["kind"] = kind["key"]
    event["tries"] = list(event.get("tries") or []) + [kind["key"]]
    event["goods"] = None
    if any(target == GOODS for target, _side, _rates in kind["effects"]):
        event["goods"] = CATEGORIES[int(rng.random() * len(CATEGORIES)) % len(CATEGORIES)]
    mark_stage(event)


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
            last = last_stage(event)
            due_stage = stage < last and since >= max(1, int(stage_days))
            due_end = stage >= last and since >= max(1, int(settle_days))
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
            mark_stage(event)
            changes.append((event, "stage", stage + 1))
    return changes


def end(bucket, event, day, by, rest_days=None, by_name=None):
    """出来事を終える。`by_name` は片付けた冒険者の名前（`END_RIVAL` のとき。終わった欄に出す）。"""
    event["status"] = ENDED
    event["ended"] = day
    event["end_by"] = by
    if by_name:
        event["end_by_name"] = by_name
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
    """次に段が変わるまでの日数（最後の段なら収まるまで）。読めなければ None。"""
    if day is None or event.get("status") != ACTIVE:
        return None
    since = day - _int(event.get("stage_day"), day)
    limit = settle_days if _int(event.get("stage"), 1) >= last_stage(event) else stage_days
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
    stage = min(max(_int(event.get("stage"), 1), 1), last_stage(event))
    total = 0
    for target, side, rates in kind["effects"]:
        types = _targets(target, event)
        if types is not None and category not in types:
            continue
        if side == "both" or (side == "buy" and key == BUY) or (side == "sell" and key == SELL):
            total += rates[min(stage, len(rates)) - 1]
    return total * max(0, int(strength_percent)) / 100.0


def inn_rate(event, strength_percent=100):
    kind = KIND_BY_KEY.get(event.get("kind")) if isinstance(event, dict) else None
    if kind is None or event.get("status") != ACTIVE or not kind.get("inn"):
        return 0
    stage = min(max(_int(event.get("stage"), 1), 1), last_stage(event))
    inn = kind["inn"]
    return inn[min(stage, len(inn)) - 1] * max(0, int(strength_percent)) / 100.0


def scaled(price, rate_percent):
    """率を掛けた額。0 を下回らない。率が 0 なら None（触らない）。"""
    if not rate_percent:
        return None
    return max(0.0, float(price) * (1.0 + rate_percent / 100.0))


# ------------------------------------------------------------ 文
def text_of(event, name, **fields):
    """LLM が書いた文、無ければ表の文。`{town}` と `{goods}` を埋める。"""
    kind = KIND_BY_KEY.get(event.get("kind"), {})
    text = event.get("text") or {}
    if name == "summary":
        # 概要は段ごと（`summary_<段>`）。書かれていない段は版1 の1文（`summary`）か最初の段の文
        written = (text.get("summary_{}".format(_int(event.get("stage"), 1)))
                   or text.get("summary") or text.get("summary_1"))
    else:
        written = text.get(name)
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


#: 影響の数字が主人公にとって得か損か（窓で数字に色を付ける）。
GOOD, BAD, MIXED = "good", "bad", "mixed"


def effect_items(event, strength_percent=100, mode=EFFECTS_NUMBER):
    """その出来事のいまの段の値段の効き目を `[(句, 数字, 得か損か)]` で返す。出さないなら空。

    数字は「数字」の出し方のときだけ（「言葉」なら空）。得か損かは主人公から見て:
    買値が上がる・売値が下がる・宿代が上がるは損、その逆は得。売り買いが同じ向きに動くのは半々。
    """
    if mode == EFFECTS_HIDDEN:
        return []
    kind = KIND_BY_KEY.get(event.get("kind")) if isinstance(event, dict) else None
    if kind is None:
        return []
    stage = min(max(_int(event.get("stage"), 1), 1), last_stage(event))
    scale = max(0, int(strength_percent)) / 100.0
    items = []
    for target, side, rates in kind["effects"]:
        rate = int(round(rates[min(stage, len(rates)) - 1] * scale))
        if not rate:
            continue
        if target is ALL:
            what = "全品"
        elif target == GOODS:
            what = GOODS_NAMES.get(event.get("goods"), "ある品")
        else:
            what = "・".join(GOODS_NAMES.get(name, name) for name in target)
        up = rate > 0
        tone = {"buy": BAD if up else GOOD, "sell": GOOD if up else BAD, "both": MIXED}[side]
        if mode == EFFECTS_WORDS:
            items.append(({"buy": "{}が値上がり" if up else "{}が値下がり",
                           "sell": "{}が高く売れる" if up else "{}が安くしか売れない",
                           "both": "{}が値上がり（売っても高い）" if up
                           else "{}が値下がり（売っても安い）"}[side].format(what), "", tone))
        else:
            label = {"buy": "の買値", "sell": "の売値", "both": "の売り買い"}[side]
            items.append(("{}{}".format(what, label), "{:+d}%".format(rate), tone))
    inn = kind.get("inn")
    if inn:
        rate = int(round(inn[min(stage, len(inn)) - 1] * scale))
        if rate:
            tone = BAD if rate > 0 else GOOD
            items.append(("宿代が値上がり" if rate > 0 else "宿代が値下がり", "", tone) if mode == EFFECTS_WORDS
                         else ("宿代", "{:+d}%".format(rate), tone))
    return items


def effect_parts(event, strength_percent=100, mode=EFFECTS_NUMBER):
    """その出来事のいまの段の値段の効き目を、窓に出す短い句の並びにする。出さないなら空。"""
    return [text + (" " + number if number else "")
            for text, number, _tone in effect_items(event, strength_percent, mode)]


def next_verb(event):
    """次に起きることの言い方。最後の段なら収まるときの言い方（「悪化する」「盛り上がる」「収まる」）。"""
    kind = kind_of(event)
    stage = _int(event.get("stage"), 1)
    if kind is None:
        return "収まる" if stage >= STAGES[-1] else "悪化する"
    if stage >= len(kind["stages"]):
        return kind.get("end") or "収まる"
    return kind["into"][stage] or "移る"


def ended_words(event):
    """終わった欄の言い方。片付けた冒険者の名前は、終えたときに出来事へ残した `end_by_name`。"""
    by = event.get("end_by")
    if by == END_PLAYER:
        return "解決した"
    if by == END_RIVAL:
        name = event.get("end_by_name")
        # 名前を残す前に終えた出来事は、名前が無い
        return "冒険者の{}が解決した".format(name) if name else "ほかの冒険者が解決した"
    if by == END_NATURAL:
        kind = kind_of(event)
        return (kind or {}).get("ended") or "自然に収まった"
    return "終わった"


def aiming_of(claims, quest):
    """その依頼を狙っている冒険者の名前。狙っていなければ None。

    `claims` は `{依頼 id: {"state", "by"}}`（ローダの窓口 `quest_claims` から組む。`state` は "aiming" / "done"）。
    """
    row = (claims or {}).get(str(quest)) if quest else None
    if isinstance(row, dict) and row.get("state") == "aiming":
        return row.get("by") or ""
    return None


def view_lines(events, day, claims, *, stage_days, settle_days, ended_days, in_words=False,
               quest_status=None, effects=EFFECTS_NUMBER, strength_percent=100, guild_names=None):
    """状況を見る窓の行。進行中（答え待ちは出さない）と、`ended_days` 日以内に終わったもの。

    `claims` は依頼を狙っている・片付けた冒険者（`aiming_of` を参照）。

    `in_words` が真なら日付をあいまいな言葉にする（設定 `WINDOW_DAYS`）。
    `effects` は値段の効き目の出し方（`EFFECTS_NUMBER` / `EFFECTS_WORDS` / `EFFECTS_HIDDEN`）。
    `guild_names` は `{土地 id: ギルドの名前}`。無い街は「その街のギルド」と書く。
    """
    guild_names = guild_names if isinstance(guild_names, dict) else {}
    active = sorted(events_of({"events": events}, ACTIVE),
                    key=lambda event: (-(_int(event.get("stage"), 1)), _int(event.get("started"), 0)))
    lines = []
    if active:
        lines.append("進行中")
    for event in active:
        lines.append("{}　{}（{}）".format(event.get("area_name") or "?", text_of(event, "name"),
                                       stage_name(event)))
        summary = text_of(event, "summary")
        if summary:
            lines.append("概要: {}{}".format(summary, "" if summary.endswith("。") else "。"))
        since = (day - _int(event.get("started"), day)) if day is not None else None
        left = days_to_next(event, day, stage_days, settle_days)
        what = next_verb(event)
        if in_words:
            lines.append("{}。{}{}かもしれない。".format(since_in_words(since), days_in_words(left), what))
        else:
            lines.append("発生から{}日。{}まで約{}日。".format(
                since if since is not None else "?", what, left if left is not None else "?"))
        parts = effect_parts(event, strength_percent, effects)
        if parts:
            lines.append("影響: {}。".format("、".join(parts)))
        quest = event.get("quest")
        if is_good(event):
            pass                                    # 良い出来事は依頼を作らない
        elif not quest:
            guild = guild_names.get(str(event.get("area"))) or "その街のギルド"
            lines.append("依頼: まだ出ていない（{}で掲示板を開くと出る）。".format(guild))
        elif aiming_of(claims, quest) is not None:
            lines.append("依頼: 掲示板に出ている。冒険者の{}が解決に向けて動き始めた。".format(
                aiming_of(claims, quest) or "誰か"))
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
        how = ended_words(event)
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


#: 窓の依頼の行の調子（描く側が色を決める）。
QUEST_PLAIN, QUEST_PENDING, QUEST_RIVAL, QUEST_GONE = "plain", "pending", "rival", "gone"


def view_model(events, day, claims, *, stage_days, settle_days, ended_days, in_words=False,
               quest_status=None, effects=EFFECTS_NUMBER, strength_percent=100, guild_names=None,
               here=None):
    """各地の出来事の窓の中身。`view_lines` と同じ材料・同じ設定から、札の形に組む（ゲームにも Kivy にも触らない）。

    返す形:

        {"here": いまいる街の名前と、その街の出来事（無ければ空）,
         "cards": [{"town", "name", "stage", "stage_name", "stages": [段の名前], "tones": [段の調子],
                    "days", "summary", "quest": (文, 調子) or None（良い出来事）,
                    "effects": [(文, 得か損か or None)]}],   # 数字だけ GOOD / BAD / MIXED
         "ended": [{"when", "town", "name", "how", "by"}]}   # by は END_PLAYER / END_RIVAL / END_NATURAL
    """
    guild_names = guild_names if isinstance(guild_names, dict) else {}
    active = sorted(events_of({"events": events}, ACTIVE),
                    key=lambda event: (-(_int(event.get("stage"), 1)), _int(event.get("started"), 0)))
    cards = []
    for event in active:
        stage = _int(event.get("stage"), 1)
        since = (day - _int(event.get("started"), day)) if day is not None else None
        left = days_to_next(event, day, stage_days, settle_days)
        what = next_verb(event)
        if in_words:
            days = "{}。{}{}かもしれない。".format(since_in_words(since), days_in_words(left), what)
        else:
            days = "発生から{}日。{}まで約{}日。".format(since if since is not None else "?", what,
                                                  left if left is not None else "?")
        effect = []
        for text, number, tone in effect_items(event, strength_percent, effects):
            effect += ([("、", None)] if effect else []) + (
                [(text + " ", None), (number, tone)] if number else [(text, None)])
        quest = event.get("quest")
        if is_good(event):
            line = None                             # 良い出来事は依頼を作らない（窓に依頼の行を出さない）
        elif not quest:
            guild = guild_names.get(str(event.get("area"))) or "その街のギルド"
            line = ("まだ出ていない（{}で掲示板を開くと出る）。".format(guild), QUEST_PENDING)
        elif aiming_of(claims, quest) is not None:
            line = ("冒険者の{}が解決に向けて動き始めた。".format(aiming_of(claims, quest) or "誰か"), QUEST_RIVAL)
        elif quest_status is not None and quest_status(quest) is None:
            line = ("掲示板から消えている。", QUEST_GONE)
        else:
            line = ("掲示板に出ている。", QUEST_PLAIN)
        summary = text_of(event, "summary")
        kind = kind_of(event)
        names = list(kind["stages"]) if kind else [STAGE_NAMES[s] for s in STAGES]
        cards.append({"town": event.get("area_name") or "?", "name": text_of(event, "name"), "stage": stage,
                      "stage_name": stage_name(event), "stages": names, "tones": list(stage_tones(event)),
                      "days": days, "summary": summary if not summary or summary.endswith("。") else summary + "。",
                      "effects": effect, "quest": line, "area": str(event.get("area"))})
    ended = [event for event in events_of({"events": events}, ENDED)
             if day is None or day - _int(event.get("ended"), day) <= ended_days]
    ended.sort(key=lambda event: -_int(event.get("ended"), 0))
    rows = []
    for event in ended:
        by = event.get("end_by")
        how = ended_words(event)
        ended_day = _int(event.get("ended"))
        ago = day - ended_day if day is not None and ended_day is not None else None
        if in_words:
            when = ago_in_words(ago)
        else:
            when = "今日" if ago is not None and ago <= 0 else ("{}日前".format(ago) if ago is not None else "?")
        rows.append({"when": when, "town": event.get("area_name") or "?", "name": text_of(event, "name"),
                     "how": how, "by": by})
    here_text = ""
    if here is not None:
        here_cards = [card for card in cards if card["area"] == str(here)]
        here_text = "（この街で{}が進行中）".format("・".join(c["name"] for c in here_cards)) if here_cards \
            else "（この街で進行中の出来事はない）"
    return {"here": here_text, "cards": cards, "ended": rows}
