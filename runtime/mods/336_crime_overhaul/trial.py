# -*- coding: utf-8 -*-
"""裁判への介入（弁護人・司法取引・判事の買収・情状）の決まり。ゲームに触らない部品。

裁判の流れは GAME.md §2.20「逮捕・裁判・服役」。
判決の画面のボタンは、懲役なら `ImprisonmentStartManager(app, 年数, 罪状, 出来事)`、
死刑なら `ExecutionPhaseManager(app)`。介入の効き目はこのボタンを直して確かにする
（判事の頼みにも書き足すが、LLM が守るとは限らない）。
"""
import math
import re

from . import rules

#: 裁判の画面・判決の画面の目印（ボタンが呼ぶクラス）。
TRIAL_SPEC = "TrialPhaseManager"
PRISON_SPEC = "ImprisonmentStartManager"
DEATH_SPEC = "ExecutionPhaseManager"
#: 懲役の判決のボタンの文言（ゲームのまま）。
PRISON_TEXTS = ("はい", "嫌だ...")

LAWYER_LABEL = "弁護人を雇う（{cost}G）"
PLEA_LABEL = "司法取引（裏の事務所を売る）"
BRIBE_LABEL = "袖の下を渡す（{cost}G・成功率 {chance}%）"
#: 画面に残った自前ボタンの残骸を見分ける頭（前方一致）。
LABEL_HEADS = ("弁護人を雇う", "司法取引（", "袖の下を渡す")

LAWYER_TEXT = "{cost}ゴールドで弁護人を雇った。弁護人が被告の側に立ち、情状を訴えはじめた。"
PLEA_TEXT = "検察に裏の事務所の内情を売った。\n見返りに刑は軽くなるが、裏の世界にはしばらく顔を出せない。"
BRIBE_OK_TEXT = "判事の袖に{cost}ゴールドを滑り込ませた。\n判事は素知らぬ顔で受け取った。"
BRIBE_CAUGHT_TEXT = "{cost}ゴールドの袖の下は突き返された。\n「贈賄の罪も裁きに加える」"
VERDICT_TEXT = "（{reasons}、判決は{before}から{after}に改められた）"
CAUGHT_TEXT = "（贈賄の罪が加わり、刑は{before}から{after}に延びた）"
BOTH_TEXT = "（{reasons}、判決は{before}から{middle}に改められた。\nだが贈賄の罪が加わり、刑は{after}に延びた）"

REASON_LAWYER = "弁護人の異議が通り"
REASON_PLEA = "司法取引が酌まれ"
REASON_BRIBE = "判事が手心を加え"
REASON_CAUGHT = "贈賄の罪が加わり"

#: 判事の頼み（`sentence_generator`）と検察の頼み（`get_sentence_sought`）の目印。
JUDGE_MARK = "の釈明は'"
PROSECUTOR_MARK = "検察官の求刑内容を設定してください"
#: 判事・検察の頼みの「人生ログの全体象」に、文ではなく人物の実体の表記が入るゲームの不具合（GAME.md §2.20）。
LIFE_LOG_RE = re.compile(r"(人生ログの全体象: )<[^<>\n]*Character object at 0x[0-9A-Fa-f]+>")

NOTE_LAWYER = ("【弁護人】被告には弁護人が付き、情状を訴えている。"
               "死刑は選ばず、懲役で裁くこと。検察の求刑より軽くすること。")
NOTE_PLEA = ("【司法取引】被告は裏社会の内情を検察に提供した。"
             "協力を酌んで死刑は選ばず、求刑の半分程度の懲役にすること。")
NOTE_BRIBE = ("【判事の事情】判事は被告から賄賂を受け取っている。"
              "表向きは厳しく装いながら、死刑は選ばず、ごく軽い懲役にすること。")
#: 贈賄の刑の加算は判決のボタンの直しで行う（`adjust` の `penalty_years`）。判事にも重くさせると二重になり、
#: 求刑25年が判事の50年＋5年＝55年になった（実機）。判事には事実だけ伝え、求刑を大きく超えないよう頼む。
NOTE_CAUGHT = ("【贈賄】被告は役人に賄賂を渡そうとして露見した。判決で台詞に触れてよいが、"
               "贈賄の分の刑は別に加えるので、求刑を大きく超えて重くしないこと。")


def verdict_of(buttons, spec_name, spec_args):
    """判決の画面なら `("death", None)` か `("imprisonment", 年数)`。違えば None。"""
    for entry in buttons or []:
        name = spec_name(entry)
        if name == DEATH_SPEC:
            return "death", None
        if name == PRISON_SPEC:
            args = spec_args(entry) or []
            years = args[0] if args else None
            if isinstance(years, (int, float)) and not isinstance(years, bool):
                return "imprisonment", int(years)
    return None


#: 求刑の型（ゲームの `get_sentence_sought` のスキーマの `DeathPenalty.type` の const）。
DEATH_PENALTY_TYPE = "death_penalty"


def requested_years(sought):
    """検察の求刑の年数。死刑の求刑・読めなければ None。"""
    request = (sought or {}).get("sentencing_request") if isinstance(sought, dict) else None
    years = request.get("years") if isinstance(request, dict) else None
    if isinstance(years, (int, float)) and not isinstance(years, bool) and years > 0:
        return int(years)
    return None


def demanded_death(sought):
    """検察が死刑を求めたか。ゲームの型では `{"type": "death_penalty"}`（年数は無い）。"""
    request = (sought or {}).get("sentencing_request") if isinstance(sought, dict) else None
    return isinstance(request, dict) and request.get("type") == DEATH_PENALTY_TYPE


def adjust(kind, years, requested, effects, cuts, death_years, penalty_years):
    """介入を当てた判決。`(種類, 年数, 理由の並び)`。変わらなければ理由は空。

    `effects` は `{"lawyer": bool, "plea": bool, "bribe": None|"ok"|"caught"}`、
    `cuts` は `{"lawyer": %, "plea": %, "bribe": %}`（求刑から減らす割合）。
    死刑を外す介入（弁護人・司法取引・買収の成功）があれば死刑は懲役に改め、
    年数は「求刑（死刑の求刑なら `death_years`）× 減らす割合を掛け合わせたもの」を上限にする。
    判事が既にそれより軽くしていれば、判事の年数のまま（二重に減らさない）。
    買収が露見したら `penalty_years` を足す。
    """
    reasons = []
    multiplier = 1.0
    for key, reason in (("lawyer", REASON_LAWYER), ("plea", REASON_PLEA)):
        if effects.get(key):
            multiplier *= max(0.0, 1.0 - max(0, cuts.get(key, 0)) / 100.0)
            reasons.append(reason)
    if effects.get("bribe") == "ok":
        multiplier *= max(0.0, 1.0 - max(0, cuts.get("bribe", 0)) / 100.0)
        reasons.append(REASON_BRIBE)
    spared = bool(reasons)
    base = requested or (years if kind == "imprisonment" else None) or death_years
    ceiling = max(1, int(math.ceil(base * multiplier))) if spared else None
    new_kind, new_years = kind, years
    if kind == "death" and spared:
        new_kind, new_years = "imprisonment", ceiling
    elif kind == "imprisonment" and spared and years is not None and years > ceiling:
        new_years = ceiling
    if effects.get("bribe") == "caught" and new_kind == "imprisonment" and penalty_years > 0:
        new_years = (new_years or 0) + int(penalty_years)
        reasons.append(REASON_CAUGHT)
    if (new_kind, new_years) == (kind, years):
        return kind, years, []
    return new_kind, new_years, reasons


def sentence_text(kind, years):
    return "死刑" if kind == "death" else "懲役{}年".format(years)


def verdict_line(kind, years, new_kind, new_years, reasons, penalty_years):
    """改めた判決を知らせる一文。減らした分と、贈賄で足した分を分けて書く。"""
    before, after = sentence_text(kind, years), sentence_text(new_kind, new_years)
    spared = [reason for reason in reasons if reason != REASON_CAUGHT]
    if REASON_CAUGHT not in reasons:
        return VERDICT_TEXT.format(reasons="、".join(spared), before=before, after=after)
    if not spared:
        return CAUGHT_TEXT.format(before=before, after=after)
    middle = sentence_text(new_kind, new_years - int(penalty_years))
    return BOTH_TEXT.format(reasons="、".join(spared), before=before, middle=middle, after=after)


def judge_notes(effects):
    """判事の頼みに書き足す介入の一文。無ければ空。"""
    notes = []
    if effects.get("lawyer"):
        notes.append(NOTE_LAWYER)
    if effects.get("plea"):
        notes.append(NOTE_PLEA)
    if effects.get("bribe") == "ok":
        notes.append(NOTE_BRIBE)
    elif effects.get("bribe") == "caught":
        notes.append(NOTE_CAUGHT)
    return "\n".join(notes)


def circumstances(here, total, achievements, limit=3):
    """情状の段。手配の重さとこの土地での活躍。"""
    lines = ["【情状】",
             "- 手配の重さ: この土地 {}・全ての土地の合計 {}（重いほど罪が重い）".format(here, total)]
    done = [str(a) for a in (achievements or []) if a][-limit:]
    if done:
        lines.append("- この土地での活躍: " + " / ".join(done))
    return "\n".join(lines)


def fix_life_log(text, replacement):
    """「人生ログの全体象」の壊れた表記を置き換える。置き換えたら `(文, True)`。"""
    if not isinstance(text, str) or not replacement:
        return text, False
    fixed, count = LIFE_LOG_RE.subn(lambda m: m.group(1) + replacement, text)
    return fixed, bool(count)


def bribe_chance(score, base_pct, per_point_pct):
    """判事が袖の下を受け取る確率（%）。魅力 15 で `base_pct`。"""
    return rules.ability_chance(score, base_pct, per_point_pct)


def fee(quest_reward, pct):
    """弁護人・袖の下の額。その土地の依頼1件の報酬 × 割合。"""
    return rules.guide_amount(quest_reward, pct)
