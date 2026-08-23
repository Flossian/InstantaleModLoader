# -*- coding: utf-8 -*-
"""計測: 賞金首狩り（手配度に応じて追手が来る）を書くために要る値を録る。

##### 何を決めるための計測か

手配度そのものは分かっている（`area_history[エリアid]['lawfulness']`。
平常 10、0 未満で犯罪者、実プレイで -40 を観測。GAME.md §2.20）。
分かっていないのは**追手をどう出すか**で、
MOD からゲームの戦闘を起こした前例がまだ1本も無い。

| 未実測 | この計測での見どころ |
| --- | --- |
| 戦闘の起こし方 | `BattleStartManager(app, enemy_type, enemy_content)` の2引数の実値。`enemy_type` の語彙と `enemy_content` の形 |
| 敵の渡し方 | `InstantaleApp.execute_battle_process(enemies)` の `enemies` の形と、呼び出し元（どの経路がここを通るか） |
| 衛兵の道 | ゲーム自身が `guard_npc_generator(area, world, npc_difficulty_level)` へ渡す難易度の実値と、それを呼んでいる関数の名前 |
| 敵の強さの決め方 | `scripts.functions:get_enemy_exp_lvl` / `get_enemy_attributes_base_point` の引数と戻り値 |
| どこで割り込むか | 遭遇を起こしうる4箇所（エリア到着・施設到着・日数経過・自由行動）で、そのとき手が空いているか |
| **戦闘に入ったときの画面の配置** | 戦闘の選択肢と敵の表示が重なる回がある（実機で `right_button_layout` と `top_info_layout_battle` が同じ矩形で両方 opacity 1）。ゲームが起こした戦闘と MOD が起こした戦闘で HUD の各枠を突き合わせ、枠を畳んでいる段（`update_enemy_display` / `turnoff_window_visibility` / `update_enemy_info`）の前後も写す |
| **割り込む先をイベントにできるか** | `set_buttons_to_normal` / `refresh_choice_buttons` がいつ何回呼ばれるか。自由入力の4つの出口（`FreeInputStart.end_process*`）から、普通の選択肢に戻るまでに何が挟まるか |

**衛兵の道が本命**。
`guard_npc_generator` と `guard_battle_summarizer` はゲームに元から在り、
記録も残っている（`output_data/…/guard_npc_generator/`。返るのは
`EnemyData` ＝ description / look / race / size / archetype）。
つまり「敵を1体その場で作って戦闘にする」道はゲーム自身が既に通していて、
賞金首狩りはその道を借りるのが一番短い。
借りるには**衛兵を呼んでいる関数の名前**が要る。それを `frames.caller()` で採る。

##### ゲームは変更しない

200番台の約束どおり読み取りだけ。
`safe=True` と握り潰しで、記録に失敗しても本体は必ず呼ぶ。
`orig` は1回だけ呼ぶ（TECH.md §6.1）。

##### 手配度は毎回まとめて写す

追手の条件は「1つの土地の手配度」と「マイナスの合計」の両方になる予定なので、
**土地ごとの値と、その2つの要約**を同じ行に残す。
どちらの閾値が遊べる値なのかは、実プレイの数字を並べてからでないと決められない。

読み方はローダの語彙（`ui.lawfulness_by_area`。`309_` と共有）。
数え方（何を手配とみなすか）だけがこの MOD の側にある。

##### 適用順

計測は修正より後（TECH.md §3.2.2）。
`308_` が `BattleStartManager.start_battle` を、
`130_` がマスターAI の4関数を包んでいるので、この計測はその外側に入る。
`enemy_type` / `enemy_content` はゲームが組み立てたまま渡ってくる
（どちらの MOD も引数を書き換えない）。

##### 出力

`out/bounty_hunter.log`（読む用）と `out/bounty_hunter.jsonl`（1件1行、突き合わせる用）。

画面の合図は数が多くなりうるので `SCREEN_SAMPLES` 件で打ち切る。
打ち切られたかは記録の件数で分かる。
"""

import datetime
import json
import time

from instantale_modloader import frames, ui

LOG_BASENAME = "bounty_hunter.log"
RECORD_BASENAME = "bounty_hunter.jsonl"

# GUI から変えられる値（同じ名前と既定値が mod.json にもある。TECH.md §3.8）。
WANTED_THRESHOLD = 0
SCALING_SAMPLES = 60
SCREEN_SAMPLES = 300

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
# 他の切り替え枠は `size=[0,0] opa=0` に畳まれているので、
# 畳む段がどこかにあるはず。それを見つけるために前後の実寸を写す。
#
# 1回目の計測で、上の3つはこの枠を**一度も動かしていない**ことが分かった
# （`枠が動いた` が0件）。動かしているのは選択肢を塗る側らしいので、そちらを足した。
# 実測では、選択肢が5つ以上の画面で `right_button_layout` が開き、
# 4つ以下の画面では畳まれている（＝溢れたぶんを右へ出す欄）。
PANEL_TARGETS = (
    "__main__:InstantaleApp.set_buttons_to_normal",
    "__main__:InstantaleApp.display_button_load",
    "scripts.hud.new_hud:InstanTaleHUD.update_button_texts",
    "scripts.hud.new_hud:InstanTaleHUD.update_enemy_display",
    "scripts.hud.new_hud:InstanTaleHUD.turnoff_window_visibility",
    "__main__:InstantaleApp.update_enemy_info",
)

# 上の段で前後を見る枠。**この2つが重なる**のが実機で見えている症状。
PANEL_KEYS = ("right_button_layout", "top_info_layout_battle",
              "top_info_layout_normal", "button_layout")

# 自由入力の出口（4つ）。
# `master_ai_facilitator` の後ではなくこちらを契機にできるかを測る。
# 4つあるのは、会話の中・クエストの中で経路が分かれるため。
FREE_INPUT_EXITS = (
    "FreeInputStart.end_process",
    "FreeInputStart.end_process_in_conversation",
    "FreeInputStart.end_process_in_quest",
    "FreeInputStart.end_process_in_conversation_in_quest",
)

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


def widget_geometry(host, limit=60):
    """HUD にぶら下がっているウィジェットの実寸を写す。

    `pos` と `size` の両方を持つ属性だけを拾う（それがウィジェットの目印）。
    戦闘に入ったときに配置が切り替わるか、どの枠が動くかを見るための材料。
    名前は決めつけない（HUD の属性名は版で変わる）。
    """
    if host is None:
        return None
    try:
        items = list(vars(host).items())
    except TypeError:
        return None
    rows = {}
    for name, value in items:
        pos = frames.attr(value, "pos", None)
        size = frames.attr(value, "size", None)
        if not (isinstance(pos, (list, tuple)) and isinstance(size, (list, tuple))):
            continue
        try:
            rows[name] = {
                "pos": [round(float(x), 1) for x in pos],
                "size": [round(float(x), 1) for x in size],
                "opacity": frames.attr(value, "opacity", None),
                "disabled": frames.attr(value, "disabled", None),
                "children": len(frames.attr(value, "children", ()) or ()),
            }
        except (TypeError, ValueError):
            continue
        if len(rows) >= limit:
            break
    return rows


def character_brief(character):
    """敵1体を数値だけで要約する。強さの合わせ方を決めるための材料。"""
    if character is None:
        return None
    return {name: frames.attr(character, name) for name in (
        "name", "experience_level", "experience_point",
        "current_hp", "max_hp", "physical_integrity")}


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


def apply(ctx):
    log_path = ctx.out_path(LOG_BASENAME)
    record_path = ctx.out_path(RECORD_BASENAME)
    write = ctx.logger(LOG_BASENAME)

    # 同じ引数の組を何度も書かないための控え。
    # 強さの式は1戦闘で何十回も呼ばれうるので、対応表としては1通り1行で足りる。
    state = {"strength": {}, "battle_by": None}

    def now():
        return datetime.datetime.now().isoformat(timespec="seconds")

    def record(row):
        try:
            with open(record_path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
        except Exception:
            ctx.log_exc("bounty probe: record failed")

    def wanted_of(app):
        """今のプレイヤーの手配度。土地ごとの値と要約を両方返す。"""
        player = getattr(app, "player", None)
        by_area = ui.lawfulness_by_area(player)
        return {"by_area": by_area,
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
    def battle_start_init(orig, self, app=None, enemy_type=None, enemy_content=None,
                          *args, **kwargs):
        """本命の1行。`enemy_type` の語彙と `enemy_content` の形。"""
        result = orig(self, app, enemy_type, enemy_content, *args, **kwargs)
        try:
            caller = frames.caller()
            state["battle_by"] = ("ゲーム(button)" if "on_button_press" in caller
                                  else "MOD/その他")
            row = {"at": now(), "phase": "BattleStartManager.__init__",
                   "enemy_type": shape(enemy_type),
                   "enemy_content": shape(enemy_content),
                   "extra_args": [shape(value, 1) for value in args],
                   "kwargs": sorted(kwargs),
                   "started_by": state["battle_by"],
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
                    "started_by": state["battle_by"],
                    "hud": widget_geometry(ui.find_hud(app))})
            write("start_battle: 敵 {}体 {}".format(
                len(briefs),
                [(key, brief.get("experience_level"), brief.get("current_hp"))
                 for key, brief in briefs.items()]))
        except Exception:
            ctx.log_exc("bounty probe: cannot record start_battle")
        return result

    @ctx.wrap("__main__:InstantaleApp.execute_battle_process", required=False,
              safe=True)
    def execute_battle_process(orig, self, enemies=None, *args, **kwargs):
        try:
            row = {"at": now(), "phase": "execute_battle_process",
                   "enemies": shape(enemies), "extra_args": len(args),
                   "caller": frames.caller()}
            record(row)
            write("execute_battle_process: {} caller={}".format(
                row["enemies"], row["caller"]))
        except Exception:
            ctx.log_exc("bounty probe: cannot record execute_battle_process")
        return orig(self, enemies, *args, **kwargs)

    @ctx.wrap("__main__:InstantaleApp.start_battle_with_in_conversation",
              required=False, safe=True)
    def battle_in_conversation(orig, self, *args, **kwargs):
        """会話から仕掛ける経路。**引数の並びが署名から読めない**ので実物を見る。"""
        try:
            row = {"at": now(), "phase": "start_battle_with_in_conversation",
                   "args": [shape(value) for value in args],
                   "kwargs": sorted(kwargs), "caller": frames.caller()}
            record(row)
            write("start_battle_with_in_conversation: args={} caller={}".format(
                row["args"], row["caller"]))
        except Exception:
            ctx.log_exc("bounty probe: cannot record the conversation battle")
        return orig(self, *args, **kwargs)

    @ctx.wrap("__main__:InstantaleApp.generate_character_from_enemy_data",
              required=False, safe=True)
    def from_enemy_data(orig, self, character_id=None, *args, **kwargs):
        """敵の素データ → `Character`。追手を1体作るならここを通ることになる。"""
        result = orig(self, character_id, *args, **kwargs)
        try:
            record({"at": now(), "phase": "generate_character_from_enemy_data",
                    "character_id": shape(character_id),
                    "result": character_brief(result), "caller": frames.caller()})
            write("generate_character_from_enemy_data({!r}) -> {}".format(
                character_id, character_brief(result)))
        except Exception:
            ctx.log_exc("bounty probe: cannot record generate_character_from_enemy_data")
        return result

    @ctx.wrap("__main__:InstantaleApp.generate_enemy_instance_from_quest_dict",
              required=False, safe=True)
    def from_quest_dict(orig, self, enemy_dict=None, *args, **kwargs):
        """クエストの敵。`EnemyData` から実体を組む入口がここだけかを見る。"""
        result = orig(self, enemy_dict, *args, **kwargs)
        try:
            record({"at": now(), "phase": "generate_enemy_instance_from_quest_dict",
                    "enemy_dict": shape(enemy_dict),
                    "extra_args": [shape(value, 1) for value in args],
                    "result": character_brief(result), "caller": frames.caller()})
            write("generate_enemy_instance_from_quest_dict: {} -> {}".format(
                shape(enemy_dict), character_brief(result)))
        except Exception:
            ctx.log_exc(
                "bounty probe: cannot record generate_enemy_instance_from_quest_dict")
        return result

    # ------------------------------------------------------------ 衛兵の道
    @ctx.wrap("scripts.llm.llm_manager:guard_npc_generator", required=False,
              safe=True)
    def guard_npc_generator(orig, area=None, world=None, npc_difficulty_level=None,
                            *args, **kwargs):
        """衛兵が湧く瞬間。**呼び出し元の名前がこの計測の主目的**。"""
        app = ui.find_app()
        before = wanted_of(app) if app is not None else {"by_area": {}, "summary": {}}
        caller = frames.caller()
        result = orig(area, world, npc_difficulty_level, *args, **kwargs)
        try:
            record({"at": now(), "phase": "guard_npc_generator",
                    "npc_difficulty_level": shape(npc_difficulty_level),
                    "area": {"id": ui.area_id_of(area),
                             "name": frames.attr(area, "name")},
                    "player": player_brief(app) if app is not None else None,
                    "wanted": before["summary"], "lawfulness": before["by_area"],
                    "result": shape(result), "caller": caller})
            write("guard_npc_generator: 難易度={!r} 土地={} 手配={} caller={}".format(
                npc_difficulty_level, ui.area_id_of(area),
                before["summary"], caller))
        except Exception:
            ctx.log_exc("bounty probe: cannot record guard_npc_generator")
        return result

    @ctx.wrap("scripts.llm.llm_manager:guard_battle_summarizer", required=False,
              safe=True)
    def guard_battle_summarizer(orig, area=None, world=None, player=None,
                                combat_log=None, *args, **kwargs):
        """衛兵との戦闘の終わり。始まりと対にして1戦の範囲を掴む。"""
        try:
            write("guard_battle_summarizer: 土地={} caller={}".format(
                ui.area_id_of(area), frames.caller()))
            record({"at": now(), "phase": "guard_battle_summarizer",
                    "area": {"id": ui.area_id_of(area),
                             "name": frames.attr(area, "name")},
                    "combat_log": shape(combat_log, 1)})
        except Exception:
            ctx.log_exc("bounty probe: cannot record guard_battle_summarizer")
        return orig(area, world, player, combat_log, *args, **kwargs)

    # -------------------------------------------------------- 敵の強さの表
    def watch_strength(name):
        @ctx.wrap("scripts.functions:{}".format(name), required=False, safe=True)
        def strength(orig, *args, **kwargs):
            result = orig(*args, **kwargs)
            try:
                if SCALING_SAMPLES > 0:
                    key = (name, args, tuple(sorted(kwargs.items())))
                    seen = state["strength"]
                    if key not in seen and len(seen) < SCALING_SAMPLES:
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
    def area_arrival(orig, self, choice_text=None, *args, **kwargs):
        result = orig(self, choice_text, *args, **kwargs)
        try:
            snapshot("到着(土地)", getattr(self, "app", None),
                     {"choice_text": frames.short(choice_text, 40)})
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
    def elapse_days(orig, self, days=None, *args, **kwargs):
        result = orig(self, days, *args, **kwargs)
        try:
            snapshot("日数経過", self, {"days": days})
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
    screen_state = {"seen": 0, "exit_at": None, "exit_name": None, "panels": 0}

    def button_classes(app):
        buttons = getattr(app, "buttons", None)
        if not isinstance(buttons, (list, tuple)):
            return None
        return [ui.spec_cls_name(entry) for entry in buttons[:MAX_KEYS]]

    def screen_row(name, app, caller):
        since = None
        if screen_state["exit_at"] is not None:
            since = round(time.monotonic() - screen_state["exit_at"], 2)
        enemies = getattr(app, "current_enemy_dict", None)
        row = {"at": now(), "phase": "画面が戻った", "func": name,
               "since_free_input": since,
               "after": screen_state["exit_name"],
               "buttons": button_classes(app),
               "busy": ui.busy_signals(app),
               "caller": caller}
        if isinstance(enemies, dict) and enemies:
            # 戦闘中の合図だけ、配置も写す（戦闘用の画面になっているかを見る）。
            row["started_by"] = state["battle_by"]
            row["hud"] = widget_geometry(ui.find_hud(app))
        return row

    def watch_screen(target):
        @ctx.wrap("__main__:{}".format(target), required=False, safe=True)
        def screen_back(orig, self, *args, **kwargs):
            result = orig(self, *args, **kwargs)
            try:
                if screen_state["seen"] < SCREEN_SAMPLES:
                    screen_state["seen"] += 1
                    row = screen_row(target.split(".")[-1], self, frames.caller())
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

    def panel_geometry(app):
        """重なりに関わる枠だけを抜き出す（全部写すと1行が長くなりすぎる）。"""
        hud = ui.find_hud(app)
        rows = widget_geometry(hud) or {}
        return {name: rows[name] for name in PANEL_KEYS if name in rows}

    def watch_panel(target):
        @ctx.wrap(target, required=False, safe=True)
        def panel(orig, self, *args, **kwargs):
            """枠を出し入れする段の前後。**この段が畳んでいるのかを見る。**"""
            app = ui.find_app()
            before = panel_geometry(app) if SCREEN_SAMPLES > 0 else None
            result = orig(self, *args, **kwargs)
            try:
                if before is not None and screen_state["panels"] < SCREEN_SAMPLES:
                    after = panel_geometry(app)
                    if after != before:
                        screen_state["panels"] += 1
                        record({"at": now(), "phase": "枠が動いた",
                                "func": target.split(":")[-1],
                                "args": [shape(value, 0) for value in args[:3]],
                                "buttons": button_classes(app),
                                "before": before, "after": after,
                                "in_battle": bool(
                                    getattr(app, "current_enemy_dict", None)),
                                "started_by": state["battle_by"],
                                "caller": frames.caller()})
                        write("枠が動いた: {} {}".format(
                            target.split(".")[-1],
                            {name: (after[name]["size"], after[name]["opacity"])
                             for name in after}))
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
                record({"at": now(), "phase": "自由入力の出口", "func": name,
                        "args": len(args), "buttons": button_classes(app),
                        "busy": ui.busy_signals(app), "caller": frames.caller()})
                write("自由入力の出口: {} busy={}".format(
                    name, ui.busy_signals(app) or "-"))
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
