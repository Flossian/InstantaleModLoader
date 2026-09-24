# -*- coding: utf-8 -*-
r"""計測: 闘技場の試合（相手の強さと報酬）。ゲームは変えない。

相手のランクの伸びと報酬を設定で決める MOD の下調べ。
版1の実機でランクの側は決まり、残っているのは報酬の側。

**ランクはその土地の依頼の難易度から決まる**（4つの闘技場・16点が一致）:

    rank(n) = round(D * (9 + 4n) / 18)     D = 土地の難易度、n = current_phase / 2

初戦は D の半分で、1試合ごとに D の 2/9 ずつ増える。頭打ちは無い。
プレイヤーのレベルには依らない（レベル1の主人公でも 30 で始まった）。

| 闘技場 | D | 実測のランク |
| --- | --- | --- |
| `331_` が建てた闘技場 | 60 | 30 / 43 / 57 / 70 / 83 / 97 |
| 素の闘技場 B | 61 | 30 / 44 |
| 素の闘技場 A | 29 | 14 / 21 / 27 |
| 以前に採った闘技場（GAME.md の旧記録） | 70 | 35 / 51 / 66 / 82 / 97 |

ほかに版1で決まったこと:

  * 難易度は `colosseum_enemy_generator` の**第4位置引数**で渡る（`kwargs` は空）
  * ゲームは決めた格を**3か所で別々に使う**（頼み文・`config.enemy_data.<phase>.data.rank`・
    敵の数値を作る `scripts.functions:get_enemy_*` の第2引数）。
    頼み文だけ書き換えても相手は弱くならない（`334_` 版1 の実機）
  * 敵のレベルは `rank + 1`。HP は同じランクでもばらつく（rank 44 で 440 と 640）
  * **報酬は `end_phase` の中で入る**（`BattleEndInColosseum.execute` →
    `instantale.py:8105`、ワーカースレッド）。Clock 待ちではないので上乗せは戻る前に書ける
  * 勝つと `current_phase` が +2。**逃げると進まない**うえ、
    次に申し込むと同じランクの相手が作り直される
    （逃げた試合は `BattleEndInColosseum` を通らない）
  * **負けると即ゲームオーバー**（レベル1で `hp = -107`。
    `in_colosseum_battle` が立ったまま `GameOverManager` へ）
  * 参加費は取らない。受付の口上は「報酬は客の賭け具合で決まる」

報酬に**乱数は乗っていない**（版2の実機。同じランク58で
セーブ → 勝つ → ロード → 勝つ を通し、相手の名前が変わっても2回とも 359）。
ただしランクだけでは決まらない（ランク30 が `D` 60・61 の初戦では 173、`D` 32 の3試合目では 177）。
施設に焼いた `data.rank` も読まない（`334_` の上限で下げても、下げる前のランクの額が出た）。

`D` は `get_quest_difficulties` が返す一覧の**平均を四捨五入した値**
（4つの闘技場で一致。中央値では合わない。平均 31.67 の土地で切り捨てと分かれた）。

読めていないこと: **報酬の式そのもの**。`D` と試合数から別に計算していると見られる。
伸びは高いランクで鈍り、**格70を超えると頭打ち**。
線形・対数・平方根・飽和型のどれもランクの点には乗らない（点は GAME.md §2.11 の表）

## 測り方

試合を1つの窓にする。`ColosseumMatchStart.execute` で開き、
`BattleEndInColosseum.end_phase` が終わるか、`BattleEndManager`（逃げたとき）か
`GameOverManager`（負けたとき）が来たら閉じる。
窓の間の文言・日数・所持金の動きを窓に積み、閉じるときに1行にまとめる。

所持金は**変わった地点で**録る（`add_text` / `elapse_days` / 各段の出入りで見張り、
前回と違えば呼び出し元つきで1行）。窓が閉じた後も `WATCH_SECONDS` 秒は見張る。
`end_phase` の戻りだけを見ると、Clock の lambda で増えるビルドでは 0 に見える。

報酬の文（`報酬として...Gを貰った。`）は窓の文言の上限とは別枠に採る。
額は `ui.parse_coin` で読む（`130_currency_unit` が表記を変えていても読める）。

施設の `config`（`current_phase` と phase ごとのランク）は、
闘技場に立つたび・試合の各段で写す。戻ることがあるかはこの列で分かる。

## 200番台の約束どおり読み取りだけ

どの包みも `orig` の戻りをそのまま返す。ゲームの値は書き換えない。
"""

import datetime
import sys
import time

from instantale_modloader import frames, ui

LOG_BASENAME = "colosseum.log"
RECORD_BASENAME = "colosseum.jsonl"

#: 窓の間に写す文言の数。超えた数だけ残す（報酬の文は別枠で全部採る）。
TEXT_LIMIT = 40

#: 窓が閉じた後も所持金を見張る長さ（秒）。Clock で遅れて増える形を捕まえるため。
WATCH_SECONDS = 180

#: 報酬の文の目印。額は `ui.parse_coin` で読む。
REWARD_MARKS = ("報酬として", "おめでとう")

#: 闘技場の `facility_type`。
ARENA_TYPE = "colosseum"

#: 戦闘のフラグ（GAME.md §2.10）。
BATTLE_FLAGS = ("in_battle", "in_boss_battle", "in_colosseum_battle")


def apply(ctx):
    write = ctx.logger(LOG_BASENAME)
    record = ctx.jsonl(RECORD_BASENAME)
    state = {"match": None, "seq": 0, "gold": None, "watch_until": 0.0,
             "enemy_type": None, "generated": [], "ending": None}

    def now():
        return datetime.datetime.now().isoformat(timespec="seconds")

    # ------------------------------------------------------------ 写し取り
    def keep(value):
        """数と短い文字列はそのまま、それ以外は `repr_value` に落とす。

        `jsonl` は後から数える表なので、ランクや所持金が `'51'` の形で入ると
        そのまま集計できない。中身の形が読めない値（キャラクタ・大きな dict）だけ
        文字列に潰す。
        """
        if isinstance(value, bool) or value is None:
            return value
        if isinstance(value, (int, float)):
            return value
        if isinstance(value, str) and len(value) <= 80:
            return value
        return frames.repr_value(value)

    def flags_of(app):
        return dict((name, bool(getattr(app, name, False))) for name in BATTLE_FLAGS)

    def player_brief(app):
        """プレイヤーの、強さに関わりそうな値。名前は決め打ちせず読めたものだけ。"""
        player = getattr(app, "player", None) if app is not None else None
        brief = {"gold": ui.gold_of(app), "day": ui.game_day(app)}
        for name in ("experience_level", "experience_point", "current_hp",
                     "max_physical_integrity", "physical_integrity", "age",
                     "ability_scores", "original_ability_scores"):
            value = getattr(player, name, None)
            if value is not None:
                brief[name] = keep(value)
        return brief

    def difficulties_of(app):
        """その土地の依頼の難易度の一覧。ゲーム自身の関数に聞く（`133_` と同じ地点）。

        **相手のランクはこの値から決まっている**（版1の実機。4つの闘技場・16点が
        `round(D * (9 + 4n) / 18)` に乗った。`D` は難易度、`n` は `current_phase` / 2）。
        `D` は**この一覧の平均を四捨五入した値**（4つの闘技場で一致。中央値では合わない）。
        報酬の式の手掛かりになるので、一覧はそのまま写し続ける。
        """
        functions = sys.modules.get("scripts.functions")
        fn = getattr(functions, "get_quest_difficulties", None) if functions else None
        if fn is None or app is None:
            return None
        try:
            values = fn(ui.current_area(app), getattr(app, "world", None))
        except Exception:
            return None
        return [v for v in (values or [])
                if isinstance(v, (int, float)) and not isinstance(v, bool)]

    def facility_of(app):
        """立っている施設（`player.location`）。id だけなら街から引く（`232_` と同じ）。"""
        player = getattr(app, "player", None) if app is not None else None
        location = getattr(player, "location", None)
        if location is None or isinstance(location, (str, int)):
            area = ui.current_area(app)
            found, _node = ui.find_facility(area, location) if area is not None else (None, None)
            return found
        return location

    def rank_of(value):
        """敵データ（`{"type":..., "data":{... "rank": N}}` の形）からランクだけ。"""
        if not isinstance(value, dict):
            return None
        data = value.get("data")
        if isinstance(data, dict):
            return data.get("rank")
        return value.get("rank")

    def name_of(value):
        if not isinstance(value, dict):
            return None
        data = value.get("data")
        if isinstance(data, dict):
            return data.get("name")
        return value.get("name")

    def arena_brief(facility):
        """闘技場の `config`（`current_phase` と phase ごとのランク）。"""
        if facility is None:
            return None
        config = getattr(facility, "config", None)
        if not isinstance(config, dict):
            return {"name": getattr(facility, "name", None),
                    "config_type": type(config).__name__}
        enemies = config.get("enemy_data")
        ranks = {}
        if isinstance(enemies, dict):
            for phase, entry in enemies.items():
                ranks[str(phase)] = {"rank": rank_of(entry), "name": name_of(entry)}
        return {"name": getattr(facility, "name", None),
                "id": getattr(facility, "id", None),
                "facility_type": ui.facility_type_of(facility),
                "current_phase": config.get("current_phase"),
                "config_keys": sorted(str(key) for key in config),
                "ranks": ranks}

    def arena_here(app):
        """立っている施設が闘技場ならその写し。よそなら None。"""
        facility = facility_of(app)
        if facility is None or ui.facility_type_of(facility) != ARENA_TYPE:
            return None
        return arena_brief(facility)

    def enemies_brief(app):
        """戦闘に出ている敵。中身の形が読めないので `repr_value` に落とす。"""
        enemies = getattr(app, "current_enemy_dict", None) if app is not None else None
        out = []
        if isinstance(enemies, dict):
            for key, value in list(enemies.items())[:6]:
                out.append({"key": key,
                            "name": keep(getattr(value, "name", None)),
                            "level": keep(
                                getattr(value, "experience_level", None)),
                            "hp": keep(getattr(value, "current_hp", None)),
                            "repr": frames.repr_value(value)})
        elif enemies is not None:
            out.append({"repr": frames.repr_value(enemies)})
        return out

    def buttons_brief(app, limit=16):
        entries = []
        buttons = getattr(app, "buttons", None) if app is not None else None
        if isinstance(buttons, (list, tuple)):
            for entry in buttons[:limit]:
                entries.append({"text": (entry or {}).get("text")
                                if isinstance(entry, dict) else repr(entry),
                                "cls": ui.spec_cls_name(entry),
                                "args": ui.spec_args(entry)})
        return entries

    # -------------------------------------------------------------- 窓と所持金
    def watching():
        return state["match"] is not None or time.monotonic() < state["watch_until"]

    def touch_gold(app, where):
        """所持金が**変わった地点**を残す。増えた場所を突き止めるのが目的。"""
        if app is None or not watching():
            return
        gold = ui.gold_of(app)
        before = state["gold"]
        state["gold"] = gold
        if not isinstance(gold, int) or not isinstance(before, int) or gold == before:
            return
        row = {"at": now(), "phase": "gold_move", "where": where,
               "before": before, "after": gold, "moved": gold - before,
               "caller": frames.caller(), "flags": flags_of(app),
               "match": (state["match"] or {}).get("seq")}
        write("gold {} -> {} ({:+d}) at {} from {}".format(
            before, gold, gold - before, where, row["caller"]))
        record(row)
        match = state["match"]
        if match is not None:
            match["gold_moves"].append({"where": where, "moved": gold - before,
                                        "caller": row["caller"]})

    def open_match(app, why):
        if state["match"] is not None:
            write("(a match window was still open when {} came; closing it)".format(why))
            close_match(app, "reopened")
        state["seq"] += 1
        match = {"seq": state["seq"], "why": why, "at": now(),
                 "started": time.monotonic(),
                 "gold_before": ui.gold_of(app), "day_before": ui.game_day(app),
                 "player_before": player_brief(app), "arena_before": arena_here(app),
                 "texts": [], "texts_dropped": 0, "dots": 0, "rewards": [],
                 "gold_moves": [], "days": [], "steps": [], "enemy": None,
                 "game_over": False}
        state["match"] = match
        state["gold"] = ui.gold_of(app)
        write("=" * 72)
        write("match #{} opened by {}: gold={} day={} arena={}".format(
            match["seq"], why, match["gold_before"], match["day_before"],
            (match["arena_before"] or {}).get("current_phase")))
        return match

    def step(app, what, detail=None):
        """窓の中で通った地点。窓の外でも1行は残す。"""
        match = state["match"]
        line = "  step {}{}".format(what, "" if detail is None else " {}".format(detail))
        if match is None:
            write("{} (outside a match window)".format(line.strip()))
        else:
            match["steps"].append(what)
            write(line)
        touch_gold(app, what)

    def close_match(app, why):
        match = state["match"]
        if match is None:
            return
        state["match"] = None
        state["watch_until"] = time.monotonic() + WATCH_SECONDS
        try:
            gold_after = ui.gold_of(app)
            row = {"at": now(), "phase": "match", "seq": match["seq"],
                   "opened_by": match["why"], "closed_by": why,
                   "gold_before": match["gold_before"], "gold_after": gold_after,
                   "gold_gained": (gold_after - match["gold_before"])
                       if (isinstance(gold_after, int)
                           and isinstance(match["gold_before"], int)) else None,
                   "gold_moves": match["gold_moves"],
                   "rewards": match["rewards"],
                   "day_before": match["day_before"], "day_after": ui.game_day(app),
                   "elapse_days_calls": match["days"],
                   "player_before": match["player_before"],
                   "player_after": player_brief(app),
                   "arena_before": match["arena_before"], "arena_after": arena_here(app),
                   "enemy": match["enemy"], "enemy_type": state["enemy_type"],
                   "game_over": match["game_over"],
                   "steps": match["steps"],
                   "texts": match["texts"], "texts_dropped": match["texts_dropped"],
                   "loading_dots": match["dots"],
                   "difficulties": difficulties_of(app),
                   "buttons_after": buttons_brief(app),
                   "seconds": round(time.monotonic() - match["started"], 1)}
            write("match #{} closed by {}: gold {} -> {} ({}) rewards={} "
                  "phase {} -> {} steps={} in {}s".format(
                      match["seq"], why, match["gold_before"], gold_after,
                      row["gold_gained"], match["rewards"],
                      (match["arena_before"] or {}).get("current_phase"),
                      (row["arena_after"] or {}).get("current_phase"),
                      len(match["steps"]), row["seconds"]))
            for text in match["texts"]:
                write("    text: {!r}".format(text))
            record(row)
        except Exception:
            ctx.log_exc("colosseum probe: cannot record the match window")

    # ------------------------------------------------------------ 受付と申し込み
    @ctx.wrap("__main__:EntryColosseumMatchManager.method", required=False, safe=True)
    def entry_method(orig, self, *args, **kwargs):
        result = orig(self, *args, **kwargs)
        try:
            app = getattr(self, "app", None) or ui.find_app()
            arena = arena_here(app)
            write("-" * 72)
            write("EntryColosseumMatchManager.method: arena={}".format(arena))
            record({"at": now(), "phase": "entry", "arena": arena,
                    "flags": flags_of(app), "difficulties": difficulties_of(app),
                    "player": player_brief(app), "buttons": buttons_brief(app)})
        except Exception:
            ctx.log_exc("colosseum probe: cannot record the entry")
        return result

    @ctx.wrap("__main__:ColosseumMatchStart.execute", required=False)
    def match_execute(orig, self, choice_text=None, *args, **kwargs):
        """試合の窓。`申し込む` を押した地点から。"""
        app = getattr(self, "app", None) or ui.find_app()
        try:
            open_match(app, "ColosseumMatchStart.execute({!r})".format(choice_text))
        except Exception:
            ctx.log_exc("colosseum probe: cannot open the match window")
        return orig(self, choice_text, *args, **kwargs)

    @ctx.wrap("__main__:ColosseumMatchStart.method", required=False, safe=True)
    def match_method(orig, self, *args, **kwargs):
        app = getattr(self, "app", None) or ui.find_app()
        before = arena_here(app)
        result = orig(self, *args, **kwargs)
        try:
            after = arena_here(app)
            step(app, "ColosseumMatchStart.method",
                 "phase {} -> {}".format((before or {}).get("current_phase"),
                                         (after or {}).get("current_phase")))
            record({"at": now(), "phase": "match_start", "arena_before": before,
                    "arena_after": after, "player": player_brief(app),
                    "flags": flags_of(app)})
        except Exception:
            ctx.log_exc("colosseum probe: cannot record the match start")
        return result

    @ctx.wrap("__main__:ColosseumMatchStart.generate_enemy_data", required=False,
              safe=True)
    def generate_enemy_data(orig, self, enemy_id=None, *args, **kwargs):
        """`enemy_id` の実値と、作られた相手のランク。前後の `config` も写す。"""
        app = getattr(self, "app", None) or ui.find_app()
        before = arena_here(app)
        started = time.monotonic()
        result = orig(self, enemy_id, *args, **kwargs)
        try:
            after = arena_here(app)
            rank = rank_of(result)
            row = {"at": now(), "phase": "generate_enemy_data",
                   "enemy_id": keep(enemy_id),
                   "args": [frames.repr_value(a) for a in args],
                   "kwargs": {k: frames.repr_value(v) for k, v in kwargs.items()},
                   "returned_rank": rank, "returned_name": name_of(result),
                   "returned_type": type(result).__name__,
                   "arena_before": before, "arena_after": after,
                   "seconds": round(time.monotonic() - started, 1)}
            write("generate_enemy_data({!r}): rank={} name={!r} phase {} -> {}".format(
                enemy_id, rank, row["returned_name"],
                (before or {}).get("current_phase"), (after or {}).get("current_phase")))
            record(row)
            match = state["match"]
            if match is not None:
                match["enemy"] = {"rank": rank, "name": row["returned_name"],
                                  "enemy_id": row["enemy_id"]}
        except Exception:
            ctx.log_exc("colosseum probe: cannot record generate_enemy_data")
        return result

    @ctx.wrap("scripts.llm.llm_manager:colosseum_enemy_generator", required=False,
              safe=True)
    def enemy_generator(orig, *args, **kwargs):
        """**難易度の渡り方**（位置か kwargs か）と実値。頼み文の出どころ。

        位置引数の並びは `(location, area, world, npc_difficulty_level)`（`targets.txt`）。
        決め打ちで `args[3]` を読まず、位置と kwargs の両方をそのまま残す。
        """
        app = ui.find_app()
        started = time.monotonic()
        result = orig(*args, **kwargs)
        try:
            difficulty = kwargs.get("npc_difficulty_level")
            if difficulty is None and len(args) >= 4:
                difficulty = args[3]
            row = {"at": now(), "phase": "enemy_generator",
                   "argc": len(args),
                   "arg_types": [type(a).__name__ for a in args],
                   "kwarg_keys": sorted(kwargs),
                   "difficulty": keep(difficulty),
                   "difficulties": difficulties_of(app),
                   "difficulty_from": "kwargs" if "npc_difficulty_level" in kwargs
                       else ("args[3]" if len(args) >= 4 else "(not found)"),
                   "location": keep(
                       getattr(args[0], "name", None) if args else None),
                   "returned_rank": rank_of(result), "returned_name": name_of(result),
                   "returned_type": type(result).__name__,
                   "caller": frames.caller(),
                   "player": player_brief(app),
                   "arena": arena_here(app),
                   "seconds": round(time.monotonic() - started, 1)}
            write("colosseum_enemy_generator: difficulty={} ({}) argc={} kwargs={} "
                  "-> rank={} name={!r} in {}s".format(
                      row["difficulty"], row["difficulty_from"], row["argc"],
                      row["kwarg_keys"], row["returned_rank"], row["returned_name"],
                      row["seconds"]))
            record(row)
        except Exception:
            ctx.log_exc("colosseum probe: cannot record the enemy generator")
        return result

    @ctx.wrap("scripts.llm.llm_manager:colosseum_battle_summarizer", required=False,
              safe=True)
    def battle_summarizer(orig, *args, **kwargs):
        """試合の締めの要約。引数の並びと、これが報酬より前か後かを見る。"""
        try:
            step(ui.find_app(), "colosseum_battle_summarizer",
                 "argc={} kwargs={}".format(len(args), sorted(kwargs)))
        except Exception:
            pass
        return orig(*args, **kwargs)

    # ---------------------------------------------------------------- 戦闘
    #: 敵の数値を作る関数（`scripts.functions`。GAME.md §2.20）。
    #: **闘技場の相手の実際の強さがここで決まる**（レベルは難易度 + 1）。
    #: `334_colosseum_custom` が上限を当てても敵のレベルが下がらなかったので、
    #: 頼み文の難易度とは別にこちらが呼ばれていると読める。引数と戻りをそのまま録る。
    ENEMY_NUMBER_FNS = ("get_enemy_exp_lvl", "get_enemy_attributes_base_point",
                        "get_enemy_count_in_quest")

    def install_enemy_number(name):
        @ctx.wrap("scripts.functions:{}".format(name), required=False, safe=True)
        def enemy_number(orig, tier=None, difficulty=None, *args, **kwargs):
            result = orig(tier, difficulty, *args, **kwargs)
            try:
                if state["match"] is not None or watching():
                    write("{}({!r}, {!r}) -> {!r} from {}".format(
                        name, keep(tier), keep(difficulty), keep(result),
                        frames.caller()))
                    record({"at": now(), "phase": "enemy_number", "fn": name,
                            "tier": keep(tier), "difficulty": keep(difficulty),
                            "returned": keep(result), "caller": frames.caller(),
                            "match": (state["match"] or {}).get("seq")})
            except Exception:
                pass
            return result

    for _fn in ENEMY_NUMBER_FNS:
        install_enemy_number(_fn)

    @ctx.wrap("__main__:BattleStartManager.__init__", required=False, safe=True)
    def battle_init(orig, self, app=None, enemy_type=None, *args, **kwargs):
        state["enemy_type"] = keep(enemy_type)
        return orig(self, app, enemy_type, *args, **kwargs)

    @ctx.wrap("__main__:BattleStartManager.start_battle", required=False, safe=True)
    def start_battle(orig, self, *args, **kwargs):
        result = orig(self, *args, **kwargs)
        try:
            app = getattr(self, "app", None) or ui.find_app()
            flags = flags_of(app)
            if flags.get("in_colosseum_battle") or state["match"] is not None:
                row = {"at": now(), "phase": "battle_start", "flags": flags,
                       "enemy_type": state["enemy_type"],
                       "enemies": enemies_brief(app), "player": player_brief(app),
                       "buttons": buttons_brief(app),
                       "match": (state["match"] or {}).get("seq")}
                write("battle_start: flags={} enemy_type={} enemies={}".format(
                    flags, state["enemy_type"], len(row["enemies"])))
                for entry in row["buttons"]:
                    write("    button: {!r} cls={}".format(entry["text"], entry["cls"]))
                record(row)
        except Exception:
            ctx.log_exc("colosseum probe: cannot record the battle start")
        return result

    @ctx.wrap("__main__:CancelBattleActionManager.cancel_action", required=False,
              safe=True)
    def cancel_action(orig, self, *args, **kwargs):
        """**降りられるか。** 闘技場の戦闘でも撤退が通るのかを見る。"""
        app = getattr(self, "app", None) or ui.find_app()
        step(app, "CancelBattleActionManager.cancel_action",
             "flags={}".format(flags_of(app)))
        result = orig(self, *args, **kwargs)
        try:
            record({"at": now(), "phase": "cancel_action", "flags": flags_of(app),
                    "player": player_brief(app), "buttons": buttons_brief(app),
                    "match": (state["match"] or {}).get("seq")})
        except Exception:
            ctx.log_exc("colosseum probe: cannot record the cancel")
        return result

    @ctx.wrap("__main__:BattlePhaseManager.check_team_annihilation", required=False,
              safe=True)
    def check_team_annihilation(orig, self, *args, **kwargs):
        result = orig(self, *args, **kwargs)
        try:
            if state["match"] is not None:
                step(getattr(self, "app", None) or ui.find_app(),
                     "check_team_annihilation", "-> {!r}".format(result))
        except Exception:
            pass
        return result

    @ctx.wrap("__main__:BattlePhaseManager.check_character_death", required=False,
              safe=True)
    def check_character_death(orig, self, index=None, character=None, *args, **kwargs):
        result = orig(self, index, character, *args, **kwargs)
        try:
            if state["match"] is not None and getattr(character, "is_player", False):
                step(getattr(self, "app", None) or ui.find_app(),
                     "check_character_death(player)",
                     "hp={!r} -> {!r}".format(
                         keep(getattr(character, "current_hp", None)),
                         result))
        except Exception:
            pass
        return result

    # ---------------------------------------------------------- 試合の終わり
    @ctx.wrap("__main__:BattleEndInColosseum.execute", required=False)
    def battle_end_execute(orig, self, choice_text=None, *args, **kwargs):
        app = getattr(self, "app", None) or ui.find_app()
        gold_before = ui.gold_of(app)
        step(app, "BattleEndInColosseum.execute", "choice={!r}".format(choice_text))
        try:
            return orig(self, choice_text, *args, **kwargs)
        finally:
            try:
                touch_gold(app, "BattleEndInColosseum.execute (returned)")
                record({"at": now(), "phase": "battle_end_execute",
                        "choice_text": choice_text,
                        "gold_before": gold_before, "gold_after": ui.gold_of(app),
                        "flags": flags_of(app), "arena": arena_here(app),
                        "match": (state["match"] or {}).get("seq")})
            except Exception:
                ctx.log_exc("colosseum probe: cannot record the colosseum battle end")

    @ctx.wrap("__main__:BattleEndInColosseum.end_phase", required=False)
    def battle_end_phase(orig, self, *args, **kwargs):
        """**報酬はここか、この後の Clock か。** 戻った地点の所持金を必ず残す。"""
        app = getattr(self, "app", None) or ui.find_app()
        gold_before = ui.gold_of(app)
        step(app, "BattleEndInColosseum.end_phase", "gold={}".format(gold_before))
        try:
            return orig(self, *args, **kwargs)
        finally:
            try:
                gold_after = ui.gold_of(app)
                touch_gold(app, "BattleEndInColosseum.end_phase (returned)")
                write("end_phase returned: gold {} -> {} flags={}".format(
                    gold_before, gold_after, flags_of(app)))
                record({"at": now(), "phase": "battle_end_phase",
                        "gold_before": gold_before, "gold_after": gold_after,
                        "gold_moved": (gold_after - gold_before)
                            if (isinstance(gold_after, int)
                                and isinstance(gold_before, int)) else None,
                        "flags": flags_of(app), "arena": arena_here(app),
                        "match": (state["match"] or {}).get("seq")})
                close_match(app, "end_phase")
            except Exception:
                ctx.log_exc("colosseum probe: cannot record end_phase")

    # ---------------------------------------------- 戦闘を終える判定の前後の差
    def brief_value(value):
        """差を比べるための短い写し。入れ物は長さ、形の読めない物は型と id。"""
        if value is None or isinstance(value, (bool, int, float)):
            return value
        if isinstance(value, str):
            return value[:40]
        if isinstance(value, (list, tuple, dict, set)):
            return "{}(len={})".format(type(value).__name__, len(value))
        return "{}#{:x}".format(type(value).__name__, id(value))

    def snapshot(obj):
        try:
            items = dict(vars(obj))
        except Exception:
            return {}
        return dict((str(key), brief_value(value)) for key, value in items.items())

    def screen_snapshot(app):
        """app と、名前に hud を含む属性の中身（プレイヤーの絵がどこに持たれているか分からないので広く）。"""
        shot = {"app." + key: value for key, value in snapshot(app).items()}
        try:
            names = [name for name in vars(app) if "hud" in str(name).lower()]
        except Exception:
            names = []
        for name in names:
            for key, value in snapshot(getattr(app, name, None)).items():
                shot["{}.{}".format(name, key)] = value
        return shot

    def diff(before, after):
        keys = sorted(set(before) | set(after))
        return dict((key, [before.get(key), after.get(key)]) for key in keys
                    if before.get(key) != after.get(key))

    @ctx.wrap("__main__:BattlePhaseManager.check_battle_end", required=False, safe=True)
    def check_battle_end(orig, self, *args, **kwargs):
        """戦闘を終える判定の戻り値と、その前後で app と HUD の何が変わったか。

        `334_colosseum_custom` が負けを逃走扱いで切り上げると、画面からプレイヤーが消え、
        敵が一覧に残って 331 の闘技場から出られなくなった（実機）。
        ゲーム自身の逃走はこの関数の中で終わり方を作り、ゲームが片付けまで進める。
        何を片付けているかを、両方の経路で同じ形に録って比べる。
        終わり方が作られた回（`battle_end_init` が立てる印）だけ書く。
        """
        app = getattr(self, "app", None) or ui.find_app()
        state["ending"] = None
        before = screen_snapshot(app) if app is not None else {}
        result = orig(self, *args, **kwargs)
        try:
            if state["ending"] is not None:
                after = screen_snapshot(app) if app is not None else {}
                changed = diff(before, after)
                write("check_battle_end -> {!r} ({}) end_type={!r}; {} change(s): {}".format(
                    brief_value(result), type(result).__name__, state["ending"],
                    len(changed), changed))
                record({"at": now(), "phase": "check_battle_end_ended",
                        "returned": frames.repr_value(result),
                        "returned_type": type(result).__name__,
                        "end_type": state["ending"], "changed": changed,
                        "enemies_after": enemies_brief(app), "flags": flags_of(app),
                        "match": (state["match"] or {}).get("seq")})
        except Exception:
            ctx.log_exc("colosseum probe: cannot record the battle end check")
        state["ending"] = None
        return result

    @ctx.wrap("__main__:BattleEndManager.__init__", required=False, safe=True)
    def battle_end_init(orig, self, app=None, end_type=None, *args, **kwargs):
        """`end_type` の実値と、誰が作ったか。

        **倒れたときに負けとして試合を終える**（`334_colosseum_custom`）には、
        ゲームが逃げたときに通るのと同じ引数でこのマネージャを起こす必要がある。
        闘技場の外の戦闘でも録る（`end_type` に何種類あるかを知りたいので）。
        """
        try:
            state["ending"] = keep(end_type)
            target = app or ui.find_app()
            write("BattleEndManager(end_type={!r}) from {}".format(
                keep(end_type), frames.caller()))
            record({"at": now(), "phase": "battle_end_init",
                    "end_type": keep(end_type), "caller": frames.caller(),
                    "flags": flags_of(target), "arena": arena_here(target),
                    "args": [frames.repr_value(a) for a in args],
                    "match": (state["match"] or {}).get("seq")})
        except Exception:
            ctx.log_exc("colosseum probe: cannot record the battle end manager")
        return orig(self, app, end_type, *args, **kwargs)

    @ctx.wrap("__main__:BattleEndManager.execute", required=False, safe=True)
    def battle_end_other_execute(orig, self, choice_text="", *args, **kwargs):
        """`execute` と `end_phase` のどちらが先かを残す（起こす側が呼ぶ順の材料）。"""
        app = getattr(self, "app", None) or ui.find_app()
        try:
            step(app, "BattleEndManager.execute",
                 "end_type={!r} choice={!r}".format(
                     keep(getattr(self, "end_type", None)), choice_text))
        except Exception:
            pass
        before = screen_snapshot(app) if app is not None else {}
        try:
            # 逃げた者の預かりと一覧の中身の形（`334_` が倒れた主人公を預けて戻させるため）。
            shapes = {}
            for name in ("escaped_member_in_battle", "party"):
                held = getattr(app, name, None)
                if isinstance(held, dict):
                    shapes[name] = dict((str(key), type(value).__name__)
                                        for key, value in held.items())
            write("BattleEndManager.execute: holding {}".format(shapes))
        except Exception:
            pass
        try:
            return orig(self, choice_text, *args, **kwargs)
        finally:
            # 終わり方そのものが何を片付けるか（`check_battle_end` の差と並べて読む）。
            try:
                changed = diff(before, screen_snapshot(app) if app is not None else {})
                write("BattleEndManager.execute: {} change(s): {}".format(len(changed), changed))
                record({"at": now(), "phase": "battle_end_manager_execute",
                        "end_type": keep(getattr(self, "end_type", None)),
                        "changed": changed, "enemies_after": enemies_brief(app),
                        "flags": flags_of(app),
                        "match": (state["match"] or {}).get("seq")})
            except Exception:
                ctx.log_exc("colosseum probe: cannot record the battle end execute")

    def install_other_end(cls_name):
        """闘技場以外の終わり方で窓を閉じる。

        **逃げると `BattleEndInColosseum` は来ない**（版1の実機。逃げた試合の窓が
        次の申し込みまで開いたままになり、`reopened` で閉じていた）。
        逃げても `current_phase` は進まず、次に申し込むと同じランクの相手が作り直される。
        """
        @ctx.wrap("__main__:{}.end_phase".format(cls_name), required=False, safe=True)
        def other_end(orig, self, *args, **kwargs):
            app = getattr(self, "app", None) or ui.find_app()
            if state["match"] is None:
                return orig(self, *args, **kwargs)          # 闘技場の外の戦闘
            end_type = keep(getattr(self, "end_type", None))
            step(app, "{}.end_phase".format(cls_name), "end_type={!r}".format(end_type))
            try:
                return orig(self, *args, **kwargs)
            finally:
                try:
                    touch_gold(app, "{}.end_phase (returned)".format(cls_name))
                    record({"at": now(), "phase": "battle_end_other", "cls": cls_name,
                            "end_type": end_type, "flags": flags_of(app),
                            "arena": arena_here(app), "player": player_brief(app),
                            "match": (state["match"] or {}).get("seq")})
                    close_match(app, "{} (end_type={})".format(cls_name, end_type))
                except Exception:
                    ctx.log_exc("colosseum probe: cannot close the window on {}".format(
                        cls_name))

    for _cls in ("BattleEndManager", "BattleEndInFreeAction"):
        install_other_end(_cls)

    @ctx.wrap("__main__:GameOverManager.__init__", required=False, safe=True)
    def game_over_init(orig, self, app=None, *args, **kwargs):
        try:
            target = app or ui.find_app()
            match = state["match"]
            if match is not None:
                match["game_over"] = True
            write("GameOverManager(): flags={} player={} from {}".format(
                flags_of(target), player_brief(target), frames.caller()))
            record({"at": now(), "phase": "game_over", "flags": flags_of(target),
                    "player": player_brief(target), "arena": arena_here(target),
                    "caller": frames.caller(), "enemy_type": state["enemy_type"],
                    "match": (match or {}).get("seq"),
                    "steps": (match or {}).get("steps")})
            if match is not None:
                close_match(target, "game over")
        except Exception:
            ctx.log_exc("colosseum probe: cannot record the game over")
        return orig(self, app, *args, **kwargs)

    # ------------------------------------------------ 施設の出入り・文言・日数
    @ctx.wrap("__main__:MovePhaseManager.move_phase", required=False, safe=True)
    def move_phase(orig, self, *args, **kwargs):
        result = orig(self, *args, **kwargs)
        try:
            app = getattr(self, "app", None) or ui.find_app()
            arena = arena_here(app)
            if arena is not None:
                write("standing in an arena: phase={} ranks={}".format(
                    arena.get("current_phase"), arena.get("ranks")))
                record({"at": now(), "phase": "facility", "arena": arena,
                        "flags": flags_of(app),
                        "difficulties": difficulties_of(app),
                        "player": player_brief(app), "buttons": buttons_brief(app)})
        except Exception:
            ctx.log_exc("colosseum probe: cannot record the arena we stand in")
        return result

    @ctx.wrap("__main__:InstantaleApp.add_text", required=False, safe=True)
    def add_text(orig, self, context=None, *args, **kwargs):
        try:
            if isinstance(context, str) and context.strip():
                reward = any(mark in context for mark in REWARD_MARKS)
                match = state["match"]
                if reward:
                    amount = ui.parse_coin(context)
                    write("reward text: {!r} -> {}".format(context, amount))
                    record({"at": now(), "phase": "reward_text", "text": context,
                            "amount": amount, "gold": ui.gold_of(self),
                            "match": (match or {}).get("seq")})
                    if match is not None:
                        match["rewards"].append({"text": context, "amount": amount})
                elif match is not None:
                    if not context.strip(".。 　"):
                        match["dots"] += 1          # 待機表示の点は数だけ
                    elif len(match["texts"]) < TEXT_LIMIT:
                        match["texts"].append(context)
                    else:
                        match["texts_dropped"] += 1
            touch_gold(self, "add_text")
        except Exception:
            pass
        return orig(self, context, *args, **kwargs)

    @ctx.wrap("__main__:InstantaleApp.elapse_days", required=False, safe=True)
    def elapse_days(orig, self, days=None, *args, **kwargs):
        try:
            match = state["match"]
            if match is not None:
                match["days"].append(keep(days))
                write("elapse_days({!r}) in match #{}".format(days, match["seq"]))
            touch_gold(self, "elapse_days")
        except Exception:
            pass
        return orig(self, days, *args, **kwargs)

    ctx.log("colosseum probe: ready ({}, {})".format(LOG_BASENAME, RECORD_BASENAME))
