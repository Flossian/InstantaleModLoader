# -*- coding: utf-8 -*-
"""脱獄（服役中の毎年の画面で備え、決行する）の決まり。ゲームに触らない部品。

服役の流れは GAME.md §2.20「逮捕・裁判・服役」（`238_probe_prison` の実機）。
年ごとの画面の「服役する」は `ImprisonmentPhaseManager(app, 残り年数, 罪状, 出来事, 刑期)` を
ボタンに載せていて、押すと1年進む（`elapse_days(365)`・年齢 +1）。
備えはこのボタンと同じ引数でゲームの服役を1年進め、見つかったときは残り年数と刑期を延ばして渡す。
"""

from . import rules

#: 年ごとの画面の目印（ゲームの「服役する」のボタンが呼ぶクラス）。
SERVE_SPEC = "ImprisonmentPhaseManager"
SERVE_TEXT = "服役する"

#: 備えの種類。`ability` は判定に使う能力値（None は金で買う）。
#: 並びは画面に出す順。
PREPS = (
    {"key": "dig", "ability": "dexterity", "label": "壁を削る（器用 {chance}%・露見 {detect}%）",
     "ok": "夜ごと寝台の下の壁を削り、腕が通るほどの穴をあけた。",
     "fail": "壁は思いのほか固く、爪が割れただけだった。"},
    {"key": "bribe", "ability": None, "label": "看守を手懐ける（{cost}G）",
     "ok": "看守に{cost}ゴールドを握らせた。見回りの刻限を漏らしてくれるようになった。",
     "fail": ""},
    {"key": "scout", "ability": "wisdom", "label": "抜け道を探る（判断 {chance}%・露見 {detect}%）",
     "ok": "労役の行き帰りに目を配り、見張りの手薄な通路を見つけた。",
     "fail": "牢の造りを探ったが、抜け道は見つからなかった。"},
)
PREP_BY_KEY = {prep["key"]: prep for prep in PREPS}

BREAK_LABEL = "脱獄を決行する（備え {prep}/{max}）"
#: 画面に残った自前ボタンの残骸を見分ける頭（`Screen.prune_stale` は前方一致）。
LABEL_HEADS = ("壁を削る", "看守を手懐ける", "抜け道を探る", "脱獄を決行する")

PREP_TEXT = "（備え {prep}/{max}）"
DETECTED_TEXT = "看守の抜き打ちで企てが露見した。備えは潰され、刑期が{years}年延びた。"
BREAK_TEXT = "鉄格子を抜け、闇に紛れて走り出した。看守たちが追ってくる！"
ESCAPED_TEXT = "追っ手を振り切り、牢獄の外へ出た。"
ESCAPED_LAW_TEXT = "{town}の官憲が、脱獄囚を追い始めた。（手配度 {before} → {after}）"
RECAPTURED_TEXT = "看守たちに取り押さえられ、牢へ引き戻された。備えは潰され、刑期が{years}年延びた。"

#: 決行の戦闘の相手の強さの下限（土地の難易度に対する %）。備えをいくら積んでもここより弱くしない。
EASE_FLOOR_PCT = 20


def is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def serve_args(args):
    """「服役する」のボタンの引数を `(残り年数, 罪状, 出来事, 刑期)` として読む。読めなければ None。

    実機の並び（`238_` の記録）: `[3, '指名手配者としての逮捕', '周辺地域で罪を重ね…', 3]`。
    """
    if not isinstance(args, (list, tuple)) or len(args) != 4:
        return None
    remaining, charges, details, term = args
    if not (is_number(remaining) and is_number(term)):
        return None
    return int(remaining), charges, details, int(term)


def extended(args, years):
    """残り年数と刑期を `years` 年延ばした引数。"""
    remaining, charges, details, term = args
    years = max(0, int(years))
    return [remaining + years, charges, details, term + years]


def prep_chance(score, base_pct, per_point_pct):
    """備えが実る確率（%）。能力値 15 で `base_pct`。"""
    return rules.ability_chance(score, base_pct, per_point_pct)


def detect_chance(prep, base_pct, per_prep_pct):
    """その年に企てが露見する確率（%）。積んだ備えが多いほど隠しにくい。"""
    pct = base_pct + max(0, int(prep)) * per_prep_pct
    return max(0, min(rules.CHANCE_CEILING, pct))


def eased_difficulty(difficulty, prep, ease_pct):
    """決行の戦闘の相手の難易度。備え1つごとに `ease_pct`% 弱くなり、下限は `EASE_FLOOR_PCT`%。"""
    if not is_number(difficulty):
        return None
    pct = max(EASE_FLOOR_PCT, 100 - max(0, int(prep)) * max(0, ease_pct))
    return max(1, int(round(difficulty * pct / 100.0)))


def bribe_cost(quest_reward, pct):
    """看守を手懐ける額。その土地の依頼1件の報酬 × 割合。"""
    return rules.guide_amount(quest_reward, pct)


def roll_prep(rng, prep, prep_max, chance, detect):
    """備え1年の結果。`(新しい備え, "ok"|"fail"|"detected")`。

    露見は備えが実ったかより先に引く（露見すれば、その年の手柄も含めて潰れる）。
    `chance` が None は金で買う備え（必ず実り、露見しない）。
    """
    if chance is not None and rng.random() * 100 < detect:
        return 0, "detected"
    if chance is None or rng.random() * 100 < chance:
        return min(prep_max, prep + 1), "ok"
    return prep, "fail"
