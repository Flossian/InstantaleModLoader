# -*- coding: utf-8 -*-
"""計測: 賞金首狩り（`316_`）を書くために要る値を録る。ゲームは変えない。

MOD からゲームの戦闘を起こした前例が無かったので、その道を測るために作った。
録れたことは GAME.md §2.20 と VERIFICATION_LOG.md §2.51 / §2.56 / §2.59 / §2.60。

| 何を録るか | 見どころ |
| --- | --- |
| 戦闘の起こし方 | `BattleStartManager(app, enemy_type, enemy_content)` の実値と呼び出し元 |
| 敵の作られ方 | `guard_npc_generator` に渡る難易度、`generate_enemy_instance_from_quest_dict` の引数、揃った敵のレベルとHP |
| 敵の強さの表 | `scripts.functions` の3関数の引数と戻り値（同じ組は1プロセスで1度だけ） |
| 割り込む場所 | 遭遇を起こしうる4箇所での手配度と、そのとき手が空いているか |
| 割り込む先 | 画面が普通の選択肢に戻る合図（`refresh_choice_buttons`）と自由入力の出口 |
| 戦闘の画面 | 選択肢を塗る段の前後で、重なりに関わる枠の位置・大きさ・濃さが動いたか |

200番台の約束どおり読み取りだけ。`safe=True` と握り潰しで、
記録に失敗しても本体は必ず1回呼ぶ。
包みは受け取った引数をそのまま本体へ渡す（キーワードで来た引数を位置へ直さない）。

手配度の読み方はローダの語彙（`ui.lawfulness_by_area`）。
数え方（何を手配とみなすか）だけがこの MOD の側にある。

出力は `out/bounty_hunter.log`（読む用）と `out/bounty_hunter.jsonl`（1件1行）。
合図と枠の記録は件数が多くなりうるので `SCREEN_SAMPLES` で打ち切る。

版3: 1か月で `.jsonl` が約41MBに積もっていたので、決着した問いのぶんを削った。

- 枠の前後は、位置・大きさ・濃さ・`disabled` だけを比べる。版2は子の数も比べていて、
  敵の表示を組み直すだけの `update_enemy_display` が「枠が動いた」を約7,600行出していた
  （約13MB。§2.60 でこの枠を触っていないと決着済み）。
  触っていない `update_enemy_display` / `update_enemy_info` は包みから外した。
  行には動いた枠の前後だけを書く
- 枠の実寸は、件数の上限とメインスレッドかを先に見てから読む。
  版2は上限に達した後も、待機中に約0.3秒ごとに来る `display_button_load` のたびに
  HUD の全属性を前後2回走査していた
- 戦闘中の合図と `start_battle` に写していた HUD の全ウィジェットの実寸（約9MB）は落とした
  （§2.59 で決着）。`画面が戻った` の行そのものは残す
- 強さの表の「同じ組は1度だけ」と合図の件数は、`sys` に置いて1プロセスで効かせた。
  版2は注入し直しのたびに数え直していて、約8,700行のうち異なる組は約300だった
- 呼び出し元の文字列は段ごとに最初の `CALLER_SAMPLES` 件だけ組む
  （`frames.caller()` は1回あたり約1ms）
- 手配度は、手配されている土地だけを写す（版2は全土地の辞書を毎回写していた）
- 1か月で一度も来なかった包み（`generate_character_from_enemy_data` /
  `start_battle_with_in_conversation` / 自由入力の出口のうち `end_process` 以外の3つ）を外した
"""

import datetime
import sys
import threading
import time

from instantale_modloader import frames, ui

LOG_BASENAME = "bounty_hunter.log"
RECORD_BASENAME = "bounty_hunter.jsonl"

# GUI から変えられる値（同じ名前と既定値が mod.json にもある。TECH.md §3.8）。
WANTED_THRESHOLD = 0
SCALING_SAMPLES = 60
SCREEN_SAMPLES = 300

# 呼び出し元の文字列を組む件数（段ごと・1プロセスあたり）。
# 経路は数件で分かるので、それより後は組まない。
CALLER_SAMPLES = 20

# 1プロセスで数を持ち越す置き場（注入し直しと遅延の当て直しで数え直さない）。
STORE_ATTR = "_instantale_probe_bounty_hunter"

# 敵の強さを決めていそうな関数。引数と戻り値の対応表を作るために見張る。
# 見つからなくても降りる（版で名前が変わりうる）。
STRENGTH_TARGETS = (
    "get_enemy_exp_lvl",
    "get_enemy_attributes_base_point",
    "get_enemy_count_in_quest",
)

# 「ゲームが普通の選択肢に戻った」の合図になりそうな入口。
# `316_bounty_hunter` は今、手が空くのをポーリングして割り込んでいるが、
# 自由入力の後には推論・画像生成・場面移動が続くことがあり、
# 静かな瞬間は**その途中にも現れる**。
# 割り込む先をポーリングではなくこの合図に変えられるかを測る。
# `set_buttons_to_normal` はこちらから外した（下の `PANEL_TARGETS` で前後まで見る）。
# 1回目の計測で、合図として使えるのは `refresh_choice_buttons` の側だと分かっている
# （そのとき旗が空。VERIFICATION_LOG.md §2.56）。
SCREEN_TARGETS = (
    "InstantaleApp.refresh_choice_buttons",
)

# 画面の枠を出し入れしている段。
# 実測では、戦闘中に**同じ矩形へ2つの枠が両方 opacity 1 で並ぶ**回がある
# （`right_button_layout` と `top_info_layout_battle`）。
# 動かしているのは `display_button_load` → `update_ui` → `update_button_texts` の系統
# （VERIFICATION_LOG.md §2.60）。`turnoff_window_visibility` も位置・大きさ・濃さを動かす。
# `update_enemy_display` / `update_enemy_info` は1か月で一度も動かしていなかったので外した（版3）。
PANEL_TARGETS = (
    "__main__:InstantaleApp.set_buttons_to_normal",
    "__main__:InstantaleApp.display_button_load",
    "scripts.hud.new_hud:InstanTaleHUD.update_button_texts",
    "scripts.hud.new_hud:InstanTaleHUD.turnoff_window_visibility",
)

# 上の段で前後を見る枠。**この2つが重なる**のが実機で見えている症状。
PANEL_KEYS = ("right_button_layout", "top_info_layout_battle",
              "top_info_layout_normal", "button_layout")

# 枠の前後で比べる項目。子の数は比べない（敵の表示の組み直しで毎回動く。版3）。
PANEL_FIELDS = ("pos", "size", "opacity", "disabled")

# 自由入力の出口。
# `master_ai_facilitator` の後ではなくこちらを契機にできるかを測る。
# 版2までは会話の中・クエストの中の出口も見ていたが、1か月で1件と0件だったので外した。
FREE_INPUT_EXITS = (
    "FreeInputStart.end_process",
)

# 引数の並び（out/recon/targets.txt より）。位置でもキーワードでも読めるように名前で持つ。
BATTLE_INIT_ARGS = ("app", "enemy_type", "enemy_content")
QUEST_DICT_ARGS = ("enemy_dict",)
GUARD_ARGS = ("area", "world", "npc_difficulty_level")
SUMMARIZER_ARGS = ("area", "world", "player", "combat_log")

# 1件の記録に写す鍵の数の上限。
# 敵1体の素データは30項目を超えるので、全部並べるとログが読めなくなる。
MAX_KEYS = 24


# ------------------------------------------------------------------ 形を写す
def shape(value, depth=2):
    """値の**形**を写す。中身そのものは出さない（型と長さと鍵だけ）。

    ソースが読めないので、引数に何が来るかは実物を見るしかない。
    ここで欲しいのは「辞書なのか・鍵は何か・要素は何個か」であって本文ではない。
    """
    if value is None or isinstance(value, (bool, int, float)):
        return {"type": type(value).__name__, "value": value}
    if isinstance(value, str):
        return {"type": "str", "len": len(value), "head": frames.short(value, 60)}
    if isinstance(value, dict):
        keys = list(value)[:MAX_KEYS]
        row = {"type": "dict", "len": len(value),
               "keys": [str(key) for key in keys],
               "key_types": sorted({type(key).__name__ for key in keys})}
        if depth > 0 and keys:
            row["first"] = shape(value[keys[0]], depth - 1)
        return row
    if isinstance(value, (list, tuple, set)):
        items = list(value)
        row = {"type": type(value).__name__, "len": len(items)}
        if depth > 0 and items:
            row["first"] = shape(items[0], depth - 1)
        return row
    row = {"type": type(value).__name__,
           "brief": frames.short(frames.describe_instance(value), 120)}
    if depth > 0:
        try:
            row["attrs"] = sorted(vars(value))[:MAX_KEYS]
        except TypeError:
            pass
    return row


def panel_geometry(hud):
    """重なりに関わる枠（`PANEL_KEYS`）の位置・大きさ・濃さを写す。

    版2は HUD の全属性を走査して `pos` と `size` を持つものを最大60件拾い、
    そこから4つを抜いていた。見る枠は決まっているので名前で直接読む（版3）。
    """
    if hud is None:
        return {}
    rows = {}
    for name in PANEL_KEYS:
        widget = frames.attr(hud, name, None)
        pos = frames.attr(widget, "pos", None)
        size = frames.attr(widget, "size", None)
        if not (isinstance(pos, (list, tuple)) and isinstance(size, (list, tuple))):
            continue
        try:
            rows[name] = {
                "pos": [round(float(x), 1) for x in pos],
                "size": [round(float(x), 1) for x in size],
                "opacity": frames.attr(widget, "opacity", None),
                "disabled": frames.attr(widget, "disabled", None),
            }
        except (TypeError, ValueError):
            continue
    return rows


def panel_changes(before, after):
    """前後で `PANEL_FIELDS` のどれかが変わった枠だけを `{枠: {"before", "after"}}` で返す。"""
    changed = {}
    for name in set(before) | set(after):
        b, a = before.get(name), after.get(name)
        if b is None or a is None:
            changed[name] = {"before": b, "after": a}
            continue
        if any(b.get(field) != a.get(field) for field in PANEL_FIELDS):
            changed[name] = {"before": b, "after": a}
    return changed


def character_brief(character):
    """敵1体を数値だけで要約する。強さの合わせ方を決めるための材料。"""
    if character is None:
        return None
    return {name: frames.attr(character, name) for name in (
        "name", "experience_level", "experience_point",
        "current_hp", "max_hp", "physical_integrity")}


def process_store():
    """1プロセスで持ち越す数の置き場。`sys` に置いて注入し直しをまたぐ（版3）。

    | 鍵 | 中身 |
    | --- | --- |
    | `strength` | 強さの表で書いた引数の組 → 戻り値 |
    | `callers` | 段の名前 → 呼び出し元を組んだ回数 |
    | `screen` | 合図の件数・枠の件数・直前の自由入力の出口 |
    | `battle_by` | 直前の戦闘を誰が起こしたか |
    """
    found = getattr(sys, STORE_ATTR, None)
    if not isinstance(found, dict):
        found = {}
        setattr(sys, STORE_ATTR, found)
    found.setdefault("strength", {})
    found.setdefault("callers", {})
    found.setdefault("screen", {"seen": 0, "exit_at": None, "exit_name": None,
                                "panels": 0})
    found.setdefault("battle_by", None)
    return found


# -------------------------------------------------------------- 手配度の要約
def wanted_summary(by_area, threshold=0):
    """土地ごとの手配度から、追手の条件になりうる数を出す。

    | 返す値 | 意味 |
    | --- | --- |
    | `areas` | 記録のある土地の数 |
    | `wanted_areas` | 手配されている土地の数（手配度が閾値未満） |
    | `worst` | いちばん重い土地の重さ（`閾値 - 手配度`。手配されていなければ 0） |
    | `total` | 手配されている土地ぶんの重さの合計 |
    | `worst_area` | いちばん重い土地の id。無ければ `None` |

    重さを「閾値との差」で数えるのは `309_office_pardon` の罰金と同じ数え方。
    手配されていない土地（平常 10）が合計を押し上げないよう、**負の側だけ**を足す。
    """
    rows = [(str(area_id), int(value))
            for area_id, value in (by_area or {}).items()
            if isinstance(value, (int, float)) and not isinstance(value, bool)]
    wanted = [(area_id, int(threshold) - value) for area_id, value in rows]
    wanted = [(area_id, weight) for area_id, weight in wanted if weight > 0]
    worst_area, worst = None, 0
    for area_id, weight in wanted:
        if weight > worst:
            worst_area, worst = area_id, weight
    return {"areas": len(rows), "wanted_areas": len(wanted), "worst": worst,
            "total": sum(weight for _area_id, weight in wanted),
            "worst_area": worst_area}


def wanted_only(by_area, threshold=0):
    """手配されている土地（閾値未満）だけの `{土地: 手配度}`。平常の土地は写さない（版3）。"""
    return {str(area_id): value for area_id, value in (by_area or {}).items()
            if isinstance(value, (int, float)) and not isinstance(value, bool)
            and value < threshold}


def apply(ctx):
    log_path = ctx.out_path(LOG_BASENAME)
    record_path = ctx.out_path(RECORD_BASENAME)
    write = ctx.logger(LOG_BASENAME)

    store = process_store()
    screen_state = store["screen"]

    def now():
        return datetime.datetime.now().isoformat(timespec="seconds")

    #: 1件1行の JSON。後から数えるための表（ローダの語彙）。
    record = ctx.jsonl(RECORD_BASENAME)

    def caller_for(phase):
        """段ごとに最初の `CALLER_SAMPLES` 件だけ呼び出し元を組む。それ以降は None。"""
        counts = store["callers"]
        if counts.get(phase, 0) >= CALLER_SAMPLES:
            return None
        counts[phase] = counts.get(phase, 0) + 1
        return frames.caller()

    def wanted_of(app):
        """今のプレイヤーの手配度。手配されている土地だけの値と要約を返す。"""
        player = getattr(app, "player", None)
        by_area = ui.lawfulness_by_area(player)
        return {"by_area": wanted_only(by_area, WANTED_THRESHOLD),
                "summary": wanted_summary(by_area, WANTED_THRESHOLD)}

    def player_brief(app):
        player = getattr(app, "player", None)
        area = ui.current_area(app)
        return {"level": frames.attr(player, "experience_level"),
                "current_hp": frames.attr(player, "current_hp"),
                "area": {"id": ui.area_id_of(area),
                         "name": frames.attr(area, "name")}}

    def snapshot(trigger, app=None, extra=None):
        """遭遇を起こしうる場所で、手配度と「手が空いているか」を1行残す。"""
        app = app if app is not None else ui.find_app()
        if app is None:
            return
        wanted = wanted_of(app)
        row = {"at": now(), "phase": trigger, "player": player_brief(app),
               "wanted": wanted["summary"], "lawfulness": wanted["by_area"],
               "busy": ui.busy_signals(app)}
        if extra:
            row.update(extra)
        record(row)
        summary = wanted["summary"]
        write("{}: 手配 {}土地 / 最重 {}({}) / 合計 {} lv={} busy={}".format(
            trigger, summary["wanted_areas"], summary["worst"],
            summary["worst_area"], summary["total"],
            row["player"]["level"], row["busy"] or "-"))

    # ------------------------------------------------------- 戦闘の起こし方
    @ctx.wrap("__main__:BattleStartManager.__init__", required=False, safe=True)
    def battle_start_init(orig, self, *args, **kwargs):
        """本命の1行。`enemy_type` の語彙と `enemy_content` の形。"""
        result = orig(self, *args, **kwargs)
        try:
            enemy_type = frames.arg(args, kwargs, "enemy_type", BATTLE_INIT_ARGS)
            enemy_content = frames.arg(args, kwargs, "enemy_content",
                                       BATTLE_INIT_ARGS)
            # 誰が起こしたかは毎回要る（316_ の見分けに使う）ので、ここだけ件数で切らない。
            caller = frames.caller()
            store["battle_by"] = ("ゲーム(button)" if "on_button_press" in caller
                                  else "MOD/その他")
            row = {"at": now(), "phase": "BattleStartManager.__init__",
                   "enemy_type": shape(enemy_type),
                   "enemy_content": shape(enemy_content),
                   "extra_args": [shape(value, 1)
                                  for value in args[len(BATTLE_INIT_ARGS):]],
                   "kwargs": sorted(kwargs),
                   "started_by": store["battle_by"],
                   "caller": caller}
            record(row)
            write("BattleStartManager: enemy_type={!r} content={} caller={}".format(
                enemy_type, row["enemy_content"], row["caller"]))
        except Exception:
            ctx.log_exc("bounty probe: cannot record BattleStartManager")
        return result

    @ctx.wrap("__main__:BattleStartManager.start_battle", required=False, safe=True)
    def battle_start(orig, self, *args, **kwargs):
        """始まった後の敵の実体。名前・レベル・HP まで揃った姿を写す。"""
        result = orig(self, *args, **kwargs)
        try:
            app = getattr(self, "app", None) or ui.find_app()
            enemies = getattr(app, "current_enemy_dict", None)
            briefs = {}
            if isinstance(enemies, dict):
                for key in list(enemies)[:MAX_KEYS]:
                    briefs[str(key)] = character_brief(enemies[key])
            record({"at": now(), "phase": "start_battle",
                    "enemies": briefs, "player": player_brief(app),
                    "wanted": wanted_of(app)["summary"],
                    "started_by": store["battle_by"]})
            write("start_battle: 敵 {}体 {}".format(
                len(briefs),
                [(key, brief.get("experience_level"), brief.get("current_hp"))
                 for key, brief in briefs.items()]))
        except Exception:
            ctx.log_exc("bounty probe: cannot record start_battle")
        return result

    @ctx.wrap("__main__:InstantaleApp.execute_battle_process", required=False,
              safe=True)
    def execute_battle_process(orig, self, *args, **kwargs):
        try:
            enemies = frames.arg(args, kwargs, "enemies", 0)
            row = {"at": now(), "phase": "execute_battle_process",
                   "enemies": shape(enemies), "extra_args": max(len(args) - 1, 0),
                   "caller": caller_for("execute_battle_process")}
            record(row)
            write("execute_battle_process: {} caller={}".format(
                row["enemies"], row["caller"]))
        except Exception:
            ctx.log_exc("bounty probe: cannot record execute_battle_process")
        return orig(self, *args, **kwargs)

    @ctx.wrap("__main__:InstantaleApp.generate_enemy_instance_from_quest_dict",
              required=False, safe=True)
    def from_quest_dict(orig, self, *args, **kwargs):
        """クエストの敵。`EnemyData` から実体を組む入口がここだけかを見る。"""
        result = orig(self, *args, **kwargs)
        try:
            enemy_dict = frames.arg(args, kwargs, "enemy_dict", QUEST_DICT_ARGS)
            record({"at": now(), "phase": "generate_enemy_instance_from_quest_dict",
                    "enemy_dict": shape(enemy_dict),
                    "extra_args": [shape(value, 1) for value in args[1:]],
                    "result": character_brief(result),
                    "caller": caller_for("generate_enemy_instance_from_quest_dict")})
            write("generate_enemy_instance_from_quest_dict: {} -> {}".format(
                shape(enemy_dict), character_brief(result)))
        except Exception:
            ctx.log_exc(
                "bounty probe: cannot record generate_enemy_instance_from_quest_dict")
        return result

    # ------------------------------------------------------------ 衛兵の道
    @ctx.wrap("scripts.llm.llm_manager:guard_npc_generator", required=False,
              safe=True)
    def guard_npc_generator(orig, *args, **kwargs):
        """衛兵が湧く瞬間。**呼び出し元の名前がこの計測の主目的**。

        手配度は本体の前に読む（衛兵と戦った後は手配度が動く）。
        読みの失敗で本体を止めないよう try に入れる（版3）。
        """
        app = None
        before = {"by_area": {}, "summary": {}}
        try:
            app = ui.find_app()
            if app is not None:
                before = wanted_of(app)
        except Exception:
            ctx.log_exc("bounty probe: cannot read the wanted level before the guard")
        result = orig(*args, **kwargs)
        try:
            area = frames.arg(args, kwargs, "area", GUARD_ARGS)
            level = frames.arg(args, kwargs, "npc_difficulty_level", GUARD_ARGS)
            caller = caller_for("guard_npc_generator")
            record({"at": now(), "phase": "guard_npc_generator",
                    "npc_difficulty_level": shape(level),
                    "area": {"id": ui.area_id_of(area),
                             "name": frames.attr(area, "name")},
                    "player": player_brief(app) if app is not None else None,
                    "wanted": before["summary"], "lawfulness": before["by_area"],
                    "result": shape(result), "caller": caller})
            write("guard_npc_generator: 難易度={!r} 土地={} 手配={} caller={}".format(
                level, ui.area_id_of(area), before["summary"], caller))
        except Exception:
            ctx.log_exc("bounty probe: cannot record guard_npc_generator")
        return result

    @ctx.wrap("scripts.llm.llm_manager:guard_battle_summarizer", required=False,
              safe=True)
    def guard_battle_summarizer(orig, *args, **kwargs):
        """衛兵との戦闘の終わり。始まりと対にして1戦の範囲を掴む。"""
        try:
            area = frames.arg(args, kwargs, "area", SUMMARIZER_ARGS)
            write("guard_battle_summarizer: 土地={} caller={}".format(
                ui.area_id_of(area), caller_for("guard_battle_summarizer")))
            record({"at": now(), "phase": "guard_battle_summarizer",
                    "area": {"id": ui.area_id_of(area),
                             "name": frames.attr(area, "name")},
                    "combat_log": shape(
                        frames.arg(args, kwargs, "combat_log", SUMMARIZER_ARGS), 1)})
        except Exception:
            ctx.log_exc("bounty probe: cannot record guard_battle_summarizer")
        return orig(*args, **kwargs)

    # -------------------------------------------------------- 敵の強さの表
    def watch_strength(name):
        @ctx.wrap("scripts.functions:{}".format(name), required=False, safe=True)
        def strength(orig, *args, **kwargs):
            result = orig(*args, **kwargs)
            try:
                if SCALING_SAMPLES > 0:
                    # 控えは `sys` にあるので、注入し直しても同じ組は書き直さない（版3）。
                    seen = store["strength"]
                    if len(seen) < SCALING_SAMPLES:
                        key = (name, args, tuple(sorted(kwargs.items())))
                        if key not in seen:
                            seen[key] = result
                            record({"at": now(), "phase": "strength", "func": name,
                                    "args": [shape(value, 0) for value in args],
                                    "kwargs": {name_: shape(value, 0)
                                               for name_, value in kwargs.items()},
                                    "result": shape(result, 1)})
                            write("{}{} -> {!r}".format(name, args, result))
            except Exception:
                # 記録に失敗しても戻り値は素通しする（戦闘の計算を止めない）。
                pass
            return result
        return strength

    for name in STRENGTH_TARGETS:
        watch_strength(name)

    # ------------------------------------------------------- 割り込みの候補
    # 4箇所とも「そこで追手を出せるか」を測るためのもの。順序も対も見ない。
    @ctx.wrap("__main__:AreaMoveManager.execute", required=False, safe=True)
    def area_arrival(orig, self, *args, **kwargs):
        result = orig(self, *args, **kwargs)
        try:
            snapshot("到着(土地)", getattr(self, "app", None),
                     {"choice_text": frames.short(
                         frames.arg(args, kwargs, "choice_text", 0), 40)})
        except Exception:
            ctx.log_exc("bounty probe: cannot record the area arrival")
        return result

    @ctx.wrap("__main__:MovePhaseManager.move_phase", required=False, safe=True)
    def facility_arrival(orig, self, *args, **kwargs):
        result = orig(self, *args, **kwargs)
        try:
            app = getattr(self, "app", None) or ui.find_app()
            facility = getattr(getattr(app, "player", None), "location", None)
            snapshot("到着(施設)", app, {"facility": ui.facility_type_of(facility)})
        except Exception:
            ctx.log_exc("bounty probe: cannot record the facility arrival")
        return result

    @ctx.wrap("__main__:InstantaleApp.elapse_days", required=False, safe=True)
    def elapse_days(orig, self, *args, **kwargs):
        result = orig(self, *args, **kwargs)
        try:
            snapshot("日数経過", self, {"days": frames.arg(args, kwargs, "days", 0)})
        except Exception:
            ctx.log_exc("bounty probe: cannot record the day elapse")
        return result

    @ctx.wrap("scripts.llm.llm_manager:master_ai_facilitator", required=False,
              safe=True)
    def free_action(orig, *args, **kwargs):
        try:
            snapshot("自由行動")
        except Exception:
            ctx.log_exc("bounty probe: cannot record the free action")
        return orig(*args, **kwargs)

    # -------------------------------------------- 画面が戻る合図（イベント化の下調べ）
    # 割り込む先をポーリングからイベントへ移せるかを測る。
    # 知りたいのは3つ。
    #   * その合図はいつ来るか（自由入力の出口から何秒後か）
    #   * 1回の場面で何度来るか（毎フレーム来るなら合図にならない）
    #   * 来たとき本当に「普通の選択肢」が並んでいるか（クラス名で見る）
    # 合図は推論のスレッドからも来る（`FreeInputStart.method` の中の `finish_button_load`）。
    # ここではウィジェットを読まない（版3で HUD の実寸を落とした）。

    def button_classes(app):
        buttons = getattr(app, "buttons", None)
        if not isinstance(buttons, (list, tuple)):
            return None
        return [ui.spec_cls_name(entry) for entry in buttons[:MAX_KEYS]]

    def screen_row(name, app, caller):
        since = None
        if screen_state["exit_at"] is not None:
            since = round(time.monotonic() - screen_state["exit_at"], 2)
        row = {"at": now(), "phase": "画面が戻った", "func": name,
               "since_free_input": since,
               "after": screen_state["exit_name"],
               "buttons": button_classes(app),
               "busy": ui.busy_signals(app),
               "caller": caller}
        enemies = getattr(app, "current_enemy_dict", None)
        if isinstance(enemies, dict) and enemies:
            row["started_by"] = store["battle_by"]
        return row

    def watch_screen(target):
        @ctx.wrap("__main__:{}".format(target), required=False, safe=True)
        def screen_back(orig, self, *args, **kwargs):
            result = orig(self, *args, **kwargs)
            try:
                if screen_state["seen"] < SCREEN_SAMPLES:
                    screen_state["seen"] += 1
                    name = target.split(".")[-1]
                    row = screen_row(name, self, caller_for(name))
                    record(row)
                    write("画面が戻った: {} 自由入力から{}秒 選択肢={} busy={}".format(
                        row["func"], row["since_free_input"], row["buttons"],
                        row["busy"] or "-"))
            except Exception:
                ctx.log_exc("bounty probe: cannot record the screen signal")
            return result
        return screen_back

    for target in SCREEN_TARGETS:
        watch_screen(target)

    def watch_panel(target):
        func_name = target.split(":")[-1]

        @ctx.wrap(target, required=False, safe=True)
        def panel(orig, self, *args, **kwargs):
            """枠を出し入れする段の前後。**この段が畳んでいるのかを見る。**

            件数の上限とメインスレッドかを**先に**見る（版3）。
            上限の後とメインスレッド以外では枠を読まずに素通しする。
            """
            app = hud = before = None
            try:
                if (screen_state["panels"] < SCREEN_SAMPLES
                        and threading.current_thread() is threading.main_thread()):
                    app = ui.find_app()
                    hud = ui.find_hud(app)
                    before = panel_geometry(hud)
            except Exception:
                before = None
            result = orig(self, *args, **kwargs)
            if before is None:
                return result
            try:
                after = panel_geometry(hud)
                changed = panel_changes(before, after)
                if changed and screen_state["panels"] < SCREEN_SAMPLES:
                    screen_state["panels"] += 1
                    record({"at": now(), "phase": "枠が動いた",
                            "func": func_name,
                            "args": [shape(value, 0) for value in args[:3]],
                            "buttons": button_classes(app),
                            "changed": changed,
                            "in_battle": bool(
                                getattr(app, "current_enemy_dict", None)),
                            "started_by": store["battle_by"],
                            "caller": caller_for(func_name)})
                    write("枠が動いた: {} {}".format(
                        func_name.split(".")[-1],
                        {name: ((entry["after"] or {}).get("size"),
                                (entry["after"] or {}).get("opacity"))
                         for name, entry in changed.items()}))
            except Exception:
                ctx.log_exc("bounty probe: cannot record the panel change")
            return result
        return panel

    for target in PANEL_TARGETS:
        watch_panel(target)

    def watch_exit(target):
        @ctx.wrap("__main__:{}".format(target), required=False, safe=True)
        def free_input_exit(orig, self, *args, **kwargs):
            """自由入力の出口。**ここを契機にできるか**を測る。"""
            name = target.split(".")[-1]
            try:
                screen_state["exit_at"] = time.monotonic()
                screen_state["exit_name"] = name
                app = getattr(self, "app", None) or ui.find_app()
                busy = ui.busy_signals(app)
                record({"at": now(), "phase": "自由入力の出口", "func": name,
                        "args": len(args), "buttons": button_classes(app),
                        "busy": busy, "caller": caller_for(name)})
                write("自由入力の出口: {} busy={}".format(name, busy or "-"))
            except Exception:
                ctx.log_exc("bounty probe: cannot record the free input exit")
            return orig(self, *args, **kwargs)
        return free_input_exit

    for target in FREE_INPUT_EXITS:
        watch_exit(target)

    # ------------------------------------------------------------ 自己検証
    # 実経路は遊んでからでないと通らないので、数え方だけ先に確かめる。
    cases = (
        # (土地ごとの手配度, 閾値, 手配された土地, 最重, 合計)
        ({"0": 10, "1": 10}, 0, 0, 0, 0),
        ({"0": -3, "1": 10}, 0, 1, 3, 3),
        ({"0": -3, "1": -40}, 0, 2, 40, 43),
        ({"0": 0}, 0, 0, 0, 0),                 # 0 は「未満」ではない（`309_` と同じ）
        ({"0": 5, "1": 10}, 10, 1, 5, 5),       # 閾値を上げれば平常も手配に数える
        ({"0": True, "1": None, "2": -1}, 0, 1, 1, 1),   # 数でない値は数えない
        ({}, 0, 0, 0, 0),
    )
    failures = []
    for by_area, threshold, wanted_areas, worst, total in cases:
        got = wanted_summary(by_area, threshold)
        if (got["wanted_areas"], got["worst"], got["total"]) != (
                wanted_areas, worst, total):
            failures.append((by_area, threshold, got))
    if failures:
        ctx.log("VERIFY FAILED: wanted_summary {}".format(failures), level="ERROR")
    else:
        ctx.log("verified: wanted_summary on {} cases".format(len(cases)))

    ctx.log("bounty hunter probe installed; log={} records={} 画面の合図={} "
            "自由入力の出口={}".format(
                log_path, record_path, len(SCREEN_TARGETS), len(FREE_INPUT_EXITS)))
