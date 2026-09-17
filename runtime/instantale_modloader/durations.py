# -*- coding: utf-8 -*-
"""ゲームの期間（暦）をローダが1箇所で持つ。

宿屋の宿泊1回の長さのように、**ゲームが決めている期間**を
変える MOD（`315_vacation_custom`）と、その期間に合わせたい MOD
（`330_real_estate` の自分の家の滞在）がある。
MOD どうしは import しない（TECH.md §3.2.3）ので、両者はここで繋がる。

    期間を変える側      durations.declare(durations.INN_STAY, fn, owner="315_…")
    期間に合わせたい側  plan = durations.inn_stay(app)     # {"months", "days", ...}

**既定はゲーム自身の式**で、変える MOD が入っていなければそれが答えになる。
変える MOD は「答えを返す関数」を置く（値ではない。年齢や設定で変わるうえ、
置き直す責任を読む側に持ち込まないため）。
読む側は置いた MOD の名前を知らない。**ここに聞くだけ**。

期間を**ゲームへ当てる側**（`elapse_days` に渡る日数の差し替え）もここが持つ。
MOD ごとに `elapse_days` を包むのをやめ、ローダが1枚だけ包んで、
望みを出した MOD の中から決める（下の「日数送りの関所」）。

    当てる側            durations.install(ctx, write)
                        durations.claim_days(owner, wish, note=fn, write=write)

同じ種類を2本の MOD が置いたら、後から置いたほうが勝ち、その旨をログに残す
（§3.3.1 と同じ「後勝ち」。順序は適用順で決まるが、どちらも変えるつもりで
入れているなら、それは本人が両方入れた結果でしかない）。

置き場は `sys` の属性（`_instantale_durations`）。注入し直しをまたいで残り、
ローダのモジュールが作り直されても消えない。名前はローダのもので、MOD は触らない。
"""
import sys

from . import log_exc

#: 宿屋の宿泊1回。答えは `{"months": int, "days": int|None, "length": str}`。
#: `months` はゲームの `VacationStartManager(app, months, quality)` に渡す値、
#: `days` は実際に進む日数（`None` なら `months * 30`）。
INN_STAY = "inn_stay"

#: 街から街への移動。答えは `{"walk_days": int, "coach_days": int, "coach_fare": int}`。
#: 置く関数は `fn(app, target_area_id=None)`（行き先で変わる。距離補正のため）。
#: 素の値は実測（`217_probe_area_move`。GAME.md §2.18）: 徒歩 `elapse_days(90)`、
#: 馬車 `elapse_days(14)` と運賃 1000G。
AREA_MOVE = "area_move"
GAME_WALK_DAYS = 90
GAME_COACH_DAYS = 14
GAME_COACH_FARE = 1000

#: 施設での訓練。答えは
#: `{"days_per_year": int, "course_years": int, "activity_years": {種類: 年}}`。
#: 置く関数は `fn(app)`。
#: 素の値は実測（`231_probe_training`、2026-09-15。GAME.md §2.17）:
#: 各段の `TrainingPhaseManager.execute` で、その活動の年数 × 365 日が1回で進む
#: （3年の活動で `elapse_days(1095)`）。開始時の残り年数は 3（観測は1軒）。
#: 代金（300 で固定）は `TrainingStartManager.execute` で引かれ、期間ではないのでここには無い。
#: 活動の年数は修行内容の選択肢で決まる（1年・2年・3年）。
TRAINING = "training"
GAME_TRAINING_DAYS_PER_YEAR = 365
GAME_TRAINING_COURSE_YEARS = 3
#: 活動の種類ごとの年数（ボタンの `(N年)` と spec の args の実測。4種）。
GAME_TRAINING_ACTIVITY_YEARS = {"simple": 1, "fundamental": 2,
                                "train_skill": 2, "learn_new_skill": 3}

#: ゲームの1ヵ月（`elapse_days(months * 30)`。GAME.md §2.17 の実測）。
DAYS_PER_MONTH = 30

#: 素のゲームの宿泊期間。3ヵ月を土台に年齢で伸び、上限6ヵ月。
#: 実測は 20代=3・31歳=4 の2点（GAME.md §2.17）。境目は `315_vacation_custom` の
#: 設定の説明と同じ（30代 +1・40代 +2・50代以上 +3）で、そこも実測ではない。
INN_STAY_BASE_MONTHS = 3
INN_STAY_MAX_MONTHS = 6
INN_STAY_AGE_BONUS = ((50, 3), (40, 2), (30, 1))

_ATTR = "_instantale_durations"


def _registry():
    """`{種類: (持ち主, 関数)}`。`sys` に置いて注入し直しをまたぐ。"""
    found = getattr(sys, _ATTR, None)
    if not isinstance(found, dict):
        found = {}
        setattr(sys, _ATTR, found)
    return found


def declare(kind, fn, owner="", write=None):
    """その種類の期間を決める関数を置く。置き換えたら前の持ち主を返す。

    `fn(app)` は答えの辞書か、**「ゲームのままでよい」なら None** を返す
    （None のときはここの既定へ落ちる）。
    """
    if not callable(fn):
        raise TypeError("declare() needs a callable, got {!r}".format(type(fn)))
    registry = _registry()
    previous = registry.get(str(kind))
    registry[str(kind)] = (str(owner or ""), fn)
    before = previous[0] if previous else None
    if write and before and before != str(owner or ""):
        write("durations: {!r} is now decided by {!r} (was {!r})".format(
            kind, owner, before))
    return before


#: `forget(owner)` のときに一緒に呼ぶ片付け。`prices` が自分のぶんを足す。
#: **登録簿を1つに寄せる代わりの口**で、片付けの入口は `forget` の1本に保つ
#: （期間と値段で `forget` を2回呼ばせない）。
_CLEANERS = []


def on_forget(fn):
    """`forget(owner)` で一緒に呼ぶ片付けを足す。`fn(owner, write=None)` は名前の一覧を返す。"""
    if callable(fn) and fn not in _CLEANERS:
        _CLEANERS.append(fn)


def forget(owner, write=None):
    """その持ち主が置いたものを全部外す（MOD を外したとき）。外した種類を返す。

    日数送りの望み（`claim_days`）も、`on_forget` で足された片付けも一緒に外す。
    """
    registry = _registry()
    gone = [kind for kind, (who, _fn) in registry.items() if who == str(owner)]
    for kind in gone:
        registry.pop(kind, None)
    if _claims().pop(str(owner), None) is not None:
        gone.append("days")
    for cleaner in list(_CLEANERS):
        try:
            gone.extend(cleaner(str(owner)) or [])
        except Exception:
            log_exc("durations: a forget cleaner failed")
    if write and gone:
        write("durations: {!r} no longer decides {}".format(owner, gone))
    return gone


def source_of(kind):
    """その種類を決めている MOD の名前。誰も置いていなければ `""`（ゲームの式）。"""
    entry = _registry().get(str(kind))
    return entry[0] if entry else ""


def ask(kind, app, write=None, **context):
    """置かれた関数に聞く。無い・None・壊れているときは None（呼ぶ側が既定へ落とす）。

    `context` はそのまま関数へ渡す（街移動の行き先など。種類ごとに決まっている）。
    """
    entry = _registry().get(str(kind))
    if not entry:
        return None
    owner, fn = entry
    try:
        answer = fn(app, **context)
    except Exception as exc:
        if write:
            write("WARN durations: {!r} from {!r} raised {}: {}; using the game's "
                  "own rule".format(kind, owner, type(exc).__name__, exc))
        return None
    if answer is None:
        return None
    if not isinstance(answer, dict):
        if write:
            write("WARN durations: {!r} from {!r} returned {!r}, not a dict; using "
                  "the game's own rule".format(kind, owner, type(answer).__name__))
        return None
    return answer


# --------------------------------------------------------------------------
# 宿屋の宿泊1回
# --------------------------------------------------------------------------
def age_of(app):
    """プレイヤーの年齢。読めなければ None。"""
    value = getattr(getattr(app, "player", None), "age", None)
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def game_inn_stay(app):
    """素のゲームの宿泊期間。変える MOD が無いときの答え。"""
    months = INN_STAY_BASE_MONTHS
    age = age_of(app)
    if age is not None:
        for floor, bonus in INN_STAY_AGE_BONUS:
            if age >= floor:
                months += bonus
                break
    months = max(1, min(months, INN_STAY_MAX_MONTHS))
    return {"months": months, "days": None, "length": "{}ヵ月".format(months),
            "source": ""}


def inn_stay(app, write=None):
    """宿屋の宿泊1回の長さ。**必ず辞書を返す**。

    変える MOD が置いていればその答え（`source` にその名前）、
    無ければゲームの式（`source` は `""`）。
    `months` は 1 以上の int に、`days` は 1 以上の int か None に均す。
    """
    answer = ask(INN_STAY, app, write=write)
    if answer is None:
        return game_inn_stay(app)
    try:
        months = max(1, int(answer.get("months")))
    except (TypeError, ValueError):
        if write:
            write("WARN durations: inn_stay from {!r} has no usable months ({!r}); "
                  "using the game's own rule".format(source_of(INN_STAY), answer))
        return game_inn_stay(app)
    days = answer.get("days")
    try:
        days = max(1, int(days)) if days is not None else None
    except (TypeError, ValueError):
        days = None
    return {"months": months, "days": days,
            "length": str(answer.get("length") or "{}ヵ月".format(months)),
            "source": source_of(INN_STAY)}


def inn_stay_days(app, write=None):
    """宿屋の宿泊1回で進む日数（`days` が無ければ `months * 30`）。"""
    plan = inn_stay(app, write=write)
    return plan["days"] if plan["days"] else plan["months"] * DAYS_PER_MONTH


# --------------------------------------------------------------------------
# 街から街への移動
# --------------------------------------------------------------------------
def game_area_move():
    """素のゲームの街移動。変える MOD が無いときの答え。"""
    return {"walk_days": GAME_WALK_DAYS, "coach_days": GAME_COACH_DAYS,
            "coach_fare": GAME_COACH_FARE, "source": ""}


def _positive(value, fallback):
    try:
        value = int(value)
    except (TypeError, ValueError):
        return fallback
    return value if value >= 1 else fallback


def area_move(app, target_area_id=None, write=None):
    """街移動の日数と運賃。**必ず辞書を返す**。

    変える MOD が置いていればその答え（`source` にその名前）、無ければ素の値。
    行き先（`target_area_id`）は置いた関数へ渡る（距離補正で変わるため）。
    項目が欠けている・読めないものは素の値で埋める。
    """
    answer = ask(AREA_MOVE, app, write=write, target_area_id=target_area_id)
    base = game_area_move()
    if answer is None:
        return base
    return {"walk_days": _positive(answer.get("walk_days"), base["walk_days"]),
            "coach_days": _positive(answer.get("coach_days"), base["coach_days"]),
            "coach_fare": _positive(answer.get("coach_fare"), base["coach_fare"]),
            "source": source_of(AREA_MOVE)}


# --------------------------------------------------------------------------
# 施設での訓練
# --------------------------------------------------------------------------
def game_training():
    """素のゲームの訓練。変える MOD が無いときの答え。"""
    return {"days_per_year": GAME_TRAINING_DAYS_PER_YEAR,
            "course_years": GAME_TRAINING_COURSE_YEARS,
            "activity_years": dict(GAME_TRAINING_ACTIVITY_YEARS), "source": ""}


def training(app, write=None):
    """訓練の暦。**必ず辞書を返す**。

    `days_per_year` は1年で進む日数、`course_years` は開始時の残り年数、
    `activity_years` は活動の種類ごとの年数（置く側が一部だけ返せば、残りは素の値で埋める）。
    """
    answer = ask(TRAINING, app, write=write)
    base = game_training()
    if answer is None:
        return base
    years = dict(base["activity_years"])
    given = answer.get("activity_years")
    if isinstance(given, dict):
        for key, value in given.items():
            years[str(key)] = _positive(value, years.get(str(key), 1))
    return {"days_per_year": _positive(answer.get("days_per_year"),
                                       base["days_per_year"]),
            "course_years": _positive(answer.get("course_years"), base["course_years"]),
            "activity_years": years, "source": source_of(TRAINING)}


def training_days(app, activity, write=None):
    """その活動1段で進む日数（年数 × 1年の日数）。知らない種類は 1年ぶん。"""
    plan = training(app, write=write)
    years = plan["activity_years"].get(str(activity), 1)
    return years * plan["days_per_year"]


# --------------------------------------------------------------------------
# 日数送り（`elapse_days`）の関所
# --------------------------------------------------------------------------
# 期間の**値**は上の窓口で決まるが、それをゲームへ当てる（`elapse_days` に渡る
# 日数を差し替える）のもローダの仕事にする。
#
#     当てる側   durations.install(ctx, write)          関所を立てる（何本が呼んでも1つ）
#                durations.claim_days(owner, wish, note=…, write=…)
#
# 以前は当てる側も MOD ごとに `elapse_days` を包んでいた（307 / 314 / 315 / 325）。
# 層が重なると、内側の MOD には**外側が既に差し替えた後の数**が来る。
# 見分ける手段が「素の値（90 / 14）と同じかどうか」しか無いので、
# `314_` は相手（`307_`）の名前と挙動を知っている必要があった。
# 包みを1枚にすると、誰が何を望んだかを1か所が全部知っているので推測が要らない。
#
# 決め方（`resolve_days`）:
#
# | 望んだ MOD | 通る値 |
# |---|---|
# | 1本 | その値（増やす方向も通る。距離補正で 90 → 270） |
# | 複数 | **いちばん早く始まった事情**が決め、残りは**頭打ちだけ**掛ける |
#
# 「早く始まったほうが決める」のは、**その日数送りを起こしたのがそちら**だから。
# `307_` の道の到着は `307_` が起こした移動で、そこに後から重なる `314_`
# （街移動一般の設定）は「それ以上には延ばさない」だけを言う立場にある。
# 各 MOD は自分の事情が始まった時刻を `since`（`time.time()`）で返す。
#: ゲームの暦を進める唯一の口（GAME.md §2.16）。ここを包むのはローダだけ。
DAYS_TARGET = "__main__:InstantaleApp.elapse_days"

_CLAIMS_ATTR = "_instantale_durations_claims"
_GATE_ATTR = "_instantale_durations_gate"


def _claims():
    """`{持ち主: {"wish", "note", "write", "seq"}}`。`sys` に置いて注入し直しをまたぐ。"""
    found = getattr(sys, _CLAIMS_ATTR, None)
    if not isinstance(found, dict):
        found = {}
        setattr(sys, _CLAIMS_ATTR, found)
    return found


def claim_days(owner, wish, note=None, write=None):
    """日数送りに手を入れる MOD を登録する。同じ持ち主の登録は差し替わる。

    `wish(app, days)` はその1回に望む日数。**関心が無ければ None**
    （窓の外・設定が素のまま）。自分の事情がいつ始まったかを言うなら
    `{"days": int, "since": time.time() の値}` を返す。
    `since` を言わない望みは最後尾に回る（＝決める側にならない）。

    `note(app, days, granted)` は決まった後に必ず呼ばれる。
    **自分が決めた回も、他所が決めた回も来る**ので、
    予算の積み上げ（`spent`）はここで行う。`orig` の前に呼ばれる。

    `write` はその MOD のログ。決まった1行はここへ出る。
    """
    if not callable(wish):
        raise TypeError("claim_days() needs a callable, got {!r}".format(type(wish)))
    claims = _claims()
    previous = claims.get(str(owner))
    claims[str(owner)] = {
        "wish": wish, "note": note, "write": write,
        # 登録順。`since` を言わない望みどうしの順序をこれで決める。
        "seq": previous["seq"] if previous else len(claims),
    }
    return previous is not None


def _warn(entry, write, message):
    """その MOD のログへ。無ければ関所のログへ。どちらも無ければ黙る。"""
    for target in (entry.get("write") if isinstance(entry, dict) else None, write):
        if callable(target):
            target("WARN " + message)
            return


def _since_of(value):
    """`since` を float に。読めなければ None（＝最後尾）。"""
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _wishes_for(app, days, write):
    """その1回に望みを出した MOD を、**早く始まった順**に並べて返す。"""
    found = []
    claims = _claims()
    for owner in sorted(claims, key=lambda name: claims[name].get("seq", 0)):
        entry = claims.get(owner)
        if entry is None:
            continue
        try:
            answer = entry["wish"](app, days)
        except Exception as exc:
            _warn(entry, write, "durations: the day wish from {!r} raised {}: {}; "
                               "ignored for this call".format(
                                   owner, type(exc).__name__, exc))
            continue
        if answer is None:
            continue
        if isinstance(answer, dict):
            value, since = answer.get("days"), _since_of(answer.get("since"))
        else:
            value, since = answer, None
        if isinstance(value, bool):
            value = None
        try:
            value = max(0, int(value))
        except (TypeError, ValueError):
            _warn(entry, write, "durations: the day wish from {!r} is {!r}, not a "
                               "number; ignored for this call".format(owner, value))
            continue
        found.append({"owner": owner, "days": value, "since": since, "entry": entry})
    # `since` を言わない望みは最後尾。言ったものどうしは早い順。
    # 同時刻なら登録順（`sorted` は安定なので、上の並びがそのまま残る）。
    found.sort(key=lambda w: (w["since"] is None, w["since"] or 0.0))
    return found


def resolve_days(app, days, write=None):
    """その1回に渡す日数を決めて返す。望みが無ければ `days` のまま。

    決めた後、望みを出した全員の `note` を呼ぶ（`orig` はこの後で呼ばれる）。
    """
    wishes = _wishes_for(app, days, write)
    if not wishes:
        return days
    decider = wishes[0]
    granted = decider["days"]
    parts = ["{} decided {}".format(decider["owner"], decider["days"])]
    for other in wishes[1:]:
        if other["days"] < granted:
            granted = other["days"]
            parts.append("{} capped to {}".format(other["owner"], other["days"]))
        else:
            parts.append("{} wanted {}".format(other["owner"], other["days"]))
    granted = max(0, int(granted))
    if granted != days or len(wishes) > 1:
        line = "days: {} -> {} ({})".format(days, granted, "; ".join(parts))
        told = []
        for wish in wishes:
            target = wish["entry"].get("write")
            if callable(target) and not any(target is seen for seen in told):
                told.append(target)
                target(line)
        if not told and callable(write):
            write(line)
    for wish in wishes:
        fn = wish["entry"].get("note")
        if not callable(fn):
            continue
        try:
            fn(app, days, granted)
        except Exception as exc:
            _warn(wish["entry"], write,
                  "durations: the day note of {!r} raised {}: {}".format(
                      wish["owner"], type(exc).__name__, exc))
    return granted


def gate():
    """関所の状態。`{"generation", "targets"}` か、立っていなければ None。"""
    done = getattr(sys, _GATE_ATTR, None)
    return done if isinstance(done, dict) else None


def install(ctx, write=None):
    """日数送りの関所を立てる。包んだ対象の名前を返す。

    日数に手を入れる MOD が `apply()` の中で呼ぶ。
    何本の MOD が呼んでも、1つの世代につき関所は1つ
    （`modfacility.install` と同じ形。TECH.md §5.8）。

    > 立てるのは「最初に呼んだ MOD の適用の時点」なので、
    > 計測（200番台）はこれより後＝外側に来る。
    > 生の日数が録れる性質は変わらない（`217_` / `218_` / `231_`）。
    """
    generation = getattr(ctx, "generation", None)
    done = gate()
    if done is not None and done.get("generation") == generation:
        return list(done.get("targets") or [])

    def elapse_days(orig, self, days, *args, **kwargs):
        """望みが出ていればその日数で、無ければ渡された日数のまま `orig` を呼ぶ。

        渡す数を差し替えるだけで、暦の進め方も日次処理もゲームのまま
        （`orig` は必ず呼ぶ。呼ばずに戻ると日数以外の後始末まで落とす）。
        0 以下の回は誰にも聞かない（ゲームが 0 を渡した回に日数を作らない）。
        """
        granted = days
        try:
            if _claims() and isinstance(days, (int, float)) \
                    and not isinstance(days, bool) and int(days) > 0:
                granted = resolve_days(self, int(days), write=write)
        except Exception:
            log_exc("durations: cannot decide the days")
            granted = days
        return orig(self, granted, *args, **kwargs)

    ctx.wrap(DAYS_TARGET, required=False)(elapse_days)
    setattr(sys, _GATE_ATTR, {"generation": generation, "targets": [DAYS_TARGET]})
    if write:
        write("durations: the day gate was declared on {}".format(DAYS_TARGET))
    return [DAYS_TARGET]
