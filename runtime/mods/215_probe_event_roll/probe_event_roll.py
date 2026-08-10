# -*- coding: utf-8 -*-
"""計測: クエスト中のミニイベント（フィールドイベント）の成否判定。

##### 何を決めるための計測か

ミニイベントの判定は「機械的で、能力値が効いていないように見え、成功率が低い」
という体感がある。`output_data/` に残った記録を突き合わせて、ここまでは
分かっている（GAME.md §2.9「フィールドイベントの成否判定」・
VERIFICATION.md §2.37）:

- `field_event_evaluator` が `credibility`(1-10) と `reference_attribute`
  （6能力値のどれか）を返し、ゲームがそれを確率に変えて `quest_event_log` に
  `<確率N%: 成功>` と書き込む。`quest_referee_event_resolve` は結果だけを受け取る
- 確率は **`credibility*10 + 20` を超えない**。半数はちょうどこの値で、
  残りは**負の差**だけが付く
- 同じ時刻の別々の判定で、参照能力値が違っても差は同じ値だったことがある。
  一方で同じキャラ・同じ能力値でも時刻が違えば差は動く

つまり差を作っているのは「そのときの状態」に見えるが、**セーブと LLM の記録
だけでは正体が決まらない**。HP・体力(physical_integrity)・負傷・レベルの
どれとも、手元のデータでは合わない組み合わせが残る。

判定そのものはコンパイル済みの `QuestEventManager` の中で起きるので読めない。
**判定の瞬間に立ち会って、その場の値を全部写す**しかない。

##### 4つの問いに答える

| 問い | 見るところ |
|---|---|
| 確率は何から作られているか | 判定の直前直後のプレイヤーの値を全部控え、`credibility*10+20` との差と突き合わせる |
| 能力値は使われているか | `Character.calculate_attribute` が判定の窓の間に呼ばれるか、呼ばれたなら引数と戻り値と**呼び出し元** |
| 参照能力値は効いているか | `reference_attribute` の値を変えずに、6能力値すべての生値と換算値を毎回控える |
| 差は状態のどれに連動するか | HP・体力・負傷・レベルを1行に並べて、複数回ぶんを並べて見る |

##### ゲームは変更しない

200番台の約束どおり読み取りだけ。`safe=True` と書き込みの握り潰しで、
記録に失敗しても本体は必ず呼ぶ。`calculate_attribute` は引数から戻り値を
決めるだけに見えるが、こちらから新しい値では**呼ばない**（既にキャラクタが
持っている能力値をそのまま渡すだけにして、ゲームの状態を触る余地を残さない）。

##### 出力

`out/event_roll.log`（読む用）と `out/event_roll.jsonl`（並べて突き合わせる用）。
どちらも `313_event_ability_check` と同じ場所に書く ― 判定を触る MOD と計測が
別々の時系列になると、差が MOD のせいか元からかを見分けられないため。
"""

import datetime
import json
import re

from instantale_modloader import frames, ui

LOG_BASENAME = "event_roll.log"
RECORD_BASENAME = "event_roll.jsonl"

# 対象2関数の引数の並び（out/recon/targets.txt より）。
#   quest_referee_event_evaluate_new(quest_data, quest_log, quest_event_log,
#       enemies_info, player, skill_dict, item_dict, player_action,
#       triggered_event, event_turn)
#   quest_referee_event_resolve(quest_data, quest_log, quest_event_log,
#       enemies_info, player, triggered_event, user_input, outcome, event_turn)
EVAL_ARGS = ("quest_data", "quest_log", "quest_event_log", "enemies_info",
             "player", "skill_dict", "item_dict", "player_action",
             "triggered_event", "event_turn")
RESOLVE_ARGS = ("quest_data", "quest_log", "quest_event_log", "enemies_info",
                "player", "triggered_event", "user_input", "outcome",
                "event_turn")

ABILITIES = ("strength", "constitution", "dexterity",
             "intelligence", "wisdom", "charisma")

# ゲームが quest_event_log に書き込む判定の印。日本語では `<確率70%: 失敗>`。
# 言語が変わると文言も変わる（ゲームは tr() を通す）ので、数字と % だけを
# 頼りに拾う。最後の1つが今回の判定。
MARK_RE = re.compile(r"<[^<>]{0,24}?(\d{1,3})\s*%\s*[:：]?\s*([^<>]{0,16})>")

# 判定の窓に限って calculate_attribute を記録する（窓の外は戦闘でも画面表示でも
# 呼ばれうる）。窓は `evaluate` の入口から **`resolve` の入口**まで ―
# `resolve` は元の関数を呼ぶ**前**に窓を閉じるので、`resolve` の本体の中の
# 呼び出しは数えない。確率は resolve に入る時点で既に決まっているので、
# 見たいものは窓の中に収まっている。
CALC_LOG_LIMIT = 40


def _get(container, name, default=None):
    """dict でも属性でも読む。`305_mini_quest` の `_get` と同じ方針。

    `quest_referee*` の戻り値の形は特定できていない（`output_data/` に残るのは
    保存側が整えた形であって、関数が返したそのものではない）。両方で通す。
    """
    if container is None:
        return default
    if isinstance(container, dict):
        return container.get(name, default)
    value = getattr(container, name, None)
    return default if value is None else value


def shape_of(value):
    """戻り値の形を1行で。最初の1回だけ残す。"""
    if isinstance(value, dict):
        return "dict{" + ", ".join(sorted(value)[:10]) + "}"
    if isinstance(value, (list, tuple)):
        return "{}[{}]".format(type(value).__name__, len(value))
    try:
        fields = sorted(vars(value))[:10]
    except Exception:
        fields = []
    return "{}({})".format(type(value).__name__, ", ".join(fields))


def arg_of(args, kwargs, names, name):
    """位置引数・キーワード引数のどちらで来ても読む。無ければ None。

    呼び出し側はコンパイル済みで読めないので、どちらの渡し方かを決め打ちできない
    （`103_fix_eventlog_trim` と同じ理由）。
    """
    if name in kwargs:
        return kwargs[name]
    try:
        index = names.index(name)
    except ValueError:
        return None
    return args[index] if len(args) > index else None


def last_mark(text):
    """`quest_event_log` の末尾にある判定の印を読む。`(確率, 結果)` か `None`。"""
    if not isinstance(text, str):
        return None
    found = MARK_RE.findall(text)
    if not found:
        return None
    percent, verdict = found[-1]
    try:
        return int(percent), verdict.strip()
    except ValueError:
        return None


def ability_scores(character):
    """生の能力値を dict で。読めた経路も一緒に返す。

    セーブでは `original_ability_scores`、実行時には `update_ability_scores()` が
    作る別名がありうる。名前を推測して外すと「能力値が読めない」と「能力値が 0」を
    見分けられなくなるので、見つけた経路そのものを記録に残す。
    """
    for name in ("ability_scores", "original_ability_scores"):
        table = getattr(character, name, None)
        if isinstance(table, dict) and table:
            return name, {key: table.get(key) for key in ABILITIES}
    # 1つずつ属性で持っている形（character_sheet.json はこの形）。
    flat = {}
    for key in ABILITIES:
        value = getattr(character, "attribute_" + key, None)
        if value is not None:
            flat[key] = value
    if flat:
        return "attribute_*", flat
    return None, {}


def snapshot(character):
    """判定の瞬間のプレイヤーを1つの dict に写す。読むだけ。"""
    if character is None:
        return {"error": "player is None"}
    source, scores = ability_scores(character)
    data = {
        "name": getattr(character, "name", None),
        "level": getattr(character, "experience_level", None),
        "hp": getattr(character, "current_hp", None),
        "max_hp": getattr(character, "max_hp", None),
        "physical_integrity": getattr(character, "physical_integrity", None),
        "max_physical_integrity": getattr(character, "max_physical_integrity", None),
        "ability_source": source,
        "abilities": scores,
        "status": frames.repr_value(getattr(character, "status", None)),
        "state": frames.repr_value(getattr(character, "state", None)),
    }
    body_parts = getattr(character, "body_parts", None)
    if isinstance(body_parts, dict):
        data["injuries"] = {
            part: {"injury": _get(info, "injury"), "stage": _get(info, "stage")}
            for part, info in body_parts.items()
            if _get(info, "injury") or _get(info, "stage", "intact") != "intact"
        }
    # 換算値は「能力値を渡すと何が返るか」の対応表そのもの。ゲームの
    # calculate_attribute を、そのキャラクタが既に持っている値だけで引く。
    calculate = getattr(character, "calculate_attribute", None)
    if callable(calculate) and scores:
        table = {}
        for key, value in scores.items():
            if value is None:
                continue
            try:
                table[key] = calculate(value)
            except Exception:
                table[key] = "<raised>"
        data["calculated"] = table
    average = getattr(character, "get_attribute_average", None)
    if callable(average):
        try:
            data["attribute_average"] = average()
        except Exception:
            data["attribute_average"] = "<raised>"
    return data


def apply(ctx):
    import sys

    # **未 import でも降りない。** `scripts.llm.llm_manager` は最初の LLM
    # リクエストまで import されない（TECH.md §3.4）。ここで早期 return すると
    # フックが1本も登録されず、**保留の見張りが立たない** ― 動くかどうかが
    # 「他の MOD が同じモジュールを保留してくれるか」に依存する。`required=False`
    # で先に登録しておけば、現れた時点でローダが当て直す（`209_` の形）。

    log_path = ctx.out_path(LOG_BASENAME)
    record_path = ctx.out_path(RECORD_BASENAME)
    state = {"open": False, "pending": None, "calc": [], "shape": None,
             "rolls": 0, "probing": False}

    write = ctx.logger(LOG_BASENAME)

    def record(row):
        try:
            with open(record_path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
        except Exception:
            ctx.log_exc("event roll: record failed")

    def snap(character):
        """`snapshot` を自分の記録から外して呼ぶ。

        `snapshot` は換算表を作るために `calculate_attribute` を6回呼ぶ。窓が
        開いている間の呼び出しは全部数える作りなので、外さないと**自分の計測で
        自分のログが埋まる** ― `101_` / `214_` が踏んだのと同じ穴。
        """
        state["probing"] = True
        try:
            return snapshot(character)
        finally:
            state["probing"] = False

    def player_of(args, kwargs, names):
        """引数のプレイヤーを使い、取れなければ app から拾う。"""
        found = arg_of(args, kwargs, names, "player")
        if found is not None:
            return found, "arg"
        app = ui.find_app()
        return getattr(app, "player", None), "app.player"

    # ---------------------------------------------------------- 説得力を控える
    @ctx.wrap("scripts.llm.llm_manager:quest_referee_event_evaluate_new",
              required=False, safe=True)
    def evaluate(orig, *args, **kwargs):
        # 窓を開ける。ここから resolve までの calculate_attribute を数える。
        state["open"] = True
        state["calc"] = []
        result = orig(*args, **kwargs)
        try:
            if state["shape"] is None:
                state["shape"] = shape_of(result)
                write("evaluate: result shape = {}".format(state["shape"]))
            result_type = _get(result, "result_type")
            kind = _get(result_type, "type")
            player, via = player_of(args, kwargs, EVAL_ARGS)
            credibility = _get(result_type, "credibility")
            row = {
                "at": datetime.datetime.now().isoformat(timespec="seconds"),
                "phase": "evaluate",
                "result_type": kind,
                "credibility": credibility,
                "reference_attribute": _get(result_type, "reference_attribute"),
                "event": frames.repr_value(_get(
                    arg_of(args, kwargs, EVAL_ARGS, "triggered_event"), "event_name")),
                "event_turn": arg_of(args, kwargs, EVAL_ARGS, "event_turn"),
                "player_action": frames.repr_value(
                    arg_of(args, kwargs, EVAL_ARGS, "player_action")),
                "player_via": via,
                "player": snap(player),
            }
            state["pending"] = row
            if kind == "roll_required":
                write("evaluate: credibility={} attribute={} -> 見込みの上限 {}% "
                      "(=credibility*10+20) event={} turn={}".format(
                          credibility, row["reference_attribute"],
                          (credibility * 10 + 20) if isinstance(credibility, int) else "?",
                          row["event"], row["event_turn"]))
            else:
                # 確定成功・確定失敗はサイコロを振らない。窓を閉じておく。
                state["open"] = False
                write("evaluate: {} (判定なし) event={}".format(kind, row["event"]))
            record(row)
        except Exception:
            ctx.log_exc("event roll: could not record the evaluation")
        return result

    # -------------------------------------------- 確率と結果を突き合わせる
    @ctx.wrap("scripts.llm.llm_manager:quest_referee_event_resolve",
              required=False, safe=True)
    def resolve(orig, *args, **kwargs):
        try:
            event_log = arg_of(args, kwargs, RESOLVE_ARGS, "quest_event_log")
            mark = last_mark(event_log)
            outcome = arg_of(args, kwargs, RESOLVE_ARGS, "outcome")
            player, via = player_of(args, kwargs, RESOLVE_ARGS)
            before = state.get("pending") or {}
            credibility = before.get("credibility")
            percent = mark[0] if mark else None
            ceiling = (credibility * 10 + 20) if isinstance(credibility, int) else None
            row = {
                "at": datetime.datetime.now().isoformat(timespec="seconds"),
                "phase": "resolve",
                "percent": percent,
                "verdict": mark[1] if mark else None,
                "outcome": frames.repr_value(outcome),
                "credibility": credibility,
                "reference_attribute": before.get("reference_attribute"),
                "ceiling": ceiling,
                "gap": (percent - ceiling) if (percent is not None and ceiling is not None) else None,
                "event_turn": arg_of(args, kwargs, RESOLVE_ARGS, "event_turn"),
                "player_via": via,
                "player_before": before.get("player"),
                "player_now": snap(player),
                "calculate_attribute_calls": state.get("calc") or [],
            }
            state["rolls"] += 1
            write("resolve: 確率{}% -> {} | credibility={} attribute={} "
                  "上限{}% 差{} | calculate_attribute の呼び出し {} 回".format(
                      percent, row["verdict"], credibility,
                      row["reference_attribute"], ceiling, row["gap"],
                      len(row["calculate_attribute_calls"])))
            if row["gap"] not in (None, 0):
                # 差が付いた回。何に連動しているかはこの1行の中にしか無い。
                write("    差の手掛かり: {}".format(json.dumps(
                    {"before": row["player_before"], "now": row["player_now"]},
                    ensure_ascii=False, default=str)[:1200]))
            for call in row["calculate_attribute_calls"]:
                write("    calculate_attribute({}) -> {}  <- {}".format(
                    call.get("arg"), call.get("result"), call.get("caller")))
            record(row)
        except Exception:
            ctx.log_exc("event roll: could not record the resolution")
        finally:
            state["open"] = False
            state["pending"] = None
        return orig(*args, **kwargs)

    # ------------------------- 能力値が判定に読まれているかを直接見る
    @ctx.wrap("scripts.characters:Character.calculate_attribute",
              required=False, safe=True)
    def calculate_attribute(orig, self, attribute_score, *args, **kwargs):
        result = orig(self, attribute_score, *args, **kwargs)
        try:
            # 窓の外（戦闘・訓練・画面表示）でも呼ばれる。判定と無関係な呼び出しで
            # ログを埋めないよう、evaluate から resolve までの間だけ数える。
            # この MOD 自身の snapshot() は state["probing"] で外してある
            # （snap() を参照）。残るのはゲームが呼んだぶんだけ。
            if (state["open"] and not state["probing"]
                    and len(state["calc"]) < CALC_LOG_LIMIT):
                state["calc"].append({
                    "arg": attribute_score,
                    "result": result,
                    "who": getattr(self, "name", None),
                    "caller": frames.caller(depth=3),
                })
        except Exception:
            ctx.log_exc("event roll: could not record calculate_attribute")
        return result

    # ------------------------------------------------------------ 自己検証
    # 実経路はクエストでイベントを1回引くまで通らない。印を読む部分だけは
    # 作ったデータで先に確かめておく（`103_` と同じ方針）。
    sample = ("〈プレイヤーの入力〉A\n描写\n<確率70%: 失敗>\n"
              "〈プレイヤーの入力〉B\n描写\n<確率80%: 成功>")
    parsed = last_mark(sample)
    english = last_mark("narration\n<probability 45%: failure>")
    if parsed == (80, "成功") and english and english[0] == 45:
        ctx.log("verified: reads the last mark (80/成功) and survives other wordings")
    else:
        ctx.log("VERIFY FAILED: parsed={!r} english={!r}".format(parsed, english),
                level="ERROR")

    ctx.log("event roll probe installed; log={} records={}".format(log_path, record_path))
