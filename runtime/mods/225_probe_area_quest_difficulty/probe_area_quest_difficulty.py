# -*- coding: utf-8 -*-
"""計測: 街を初めて訪ねたとき、その街の依頼の難易度を誰がどう決めているかを録る。

街の中身（nodes・依頼3件・BGM）は初訪問のときに作られる（実データ。VERIFICATION.md §3.49 #5）。
依頼の難易度は LLM が決めているのではなく、ゲームが先に決めて頼み文に書いている:

    settlement_quest_generator(world_overview, settlement_name, ..., quest_difficulties)
      user: - 想定段階: ゲーム序盤
            - quest_1:難易度は26/70

5世界の実値は街の id ごとにほぼ同じ帯（id1≈5 / id2≈25 / id3≈50 / id4≈14 / id5≈31 /
id6≈62 / id7≈43 / id8≈70）なので、決め方はゲームのコードにある。
関数の一覧には表の定数が無く、Nuitka なので中身は読めない。
分かれば `133_ui_area_difficulty` が訪問前の街にも帯を出せる（同じ関数を呼ぶか、同じ式で見積もる）。

| 何を録るか | 見どころ |
| --- | --- |
| `settlement_quest_generator` / `random_quest_generator` / `create_settlement_detail` の実引数 | `quest_difficulties` / `quest_difficulty` の実値と、呼び出し元の連鎖 |
| `scripts.functions` の全関数（画面用と文言用を除く47本） | 移動の窓の間にどれが呼ばれるか。引数と戻り値 |
| `Area.update` / `Area.generate_nodes` / `World.generate_quests` / `generate_quest_area` / `write_area_data_to_world_dict` | 街が作られる経路の順番と、前後の `level_of_detail`・依頼の数 |
| 到着の移動（`AreaMoveManager.execute`）の間の `random.*` / `Random.*` / `numpy.random.*` | 難易度の乱数の幅（実引数と呼び出し元） |

200番台の約束どおり読み取りだけ。`safe=True` と握り潰しで、
記録に失敗しても本体は必ず1回呼ぶ。
`random` は包むだけで列は変えない（引数と戻り値を写して素通し）。

出力は `out/area_quest_difficulty.log`（読む用）と `out/area_quest_difficulty.jsonl`（1件1行）。

1回目（2026-09-03、新テストワールドの id 2 へ馬車で移動。VERIFICATION_LOG.md §2.84）で分かったこと:

- 街は `AreaMoveManager.method_1` → `save_area_json:write_area_data_to_world_dict(world_dict, area_id)`
  の中で作られる（`create_settlement_detail` が :80、`settlement_quest_generator` が :335）。
  `level_of_detail` 0 → 1、依頼 `[26, 21, 25]`
- **Nuitka のフレームは `f_locals` が空**。ローカル変数の写しは取れない（版2で外した）
- その間に `randint` / `uniform` / `choice` / `random` / `randrange` / `gauss` も
  `get_quest_difficulty_from_stat` / `get_quest_appropriate_stat` も呼ばれなかった。
  難易度はそれ以外の何かで決まっている。版2は `random` の全関数と `Random` のメソッド、
  numpy.random、`scripts.functions` の全関数（画面用を除く）を移動の窓の間だけ写す

2回目（同日、id 3 へ。難易度 `[52, 47, 51]`）: `scripts.functions` は :80〜:335 の間に1本も呼ばれない。
乱数は上限300件が窓の開始から2秒で尽きた（`132_` の種の抽選 72 件と、その内側の
`getrandbits` 228 件）ので、肝心の場面は写っていない。版3で MOD 自身の抽選と
`random` の内側を数えないようにし、上限を 1000 にした。
"""

import datetime
import json
import sys
import time

from instantale_modloader import frames, ui

LOG_BASENAME = "area_quest_difficulty.log"
RECORD_BASENAME = "area_quest_difficulty.jsonl"

# GUI から変えられる値（同じ名前と既定値が mod.json にもある。TECH.md §3.8）。
# 到着の移動の間に写す `random.*` の呼び出しの上限。0 で録らない。
# MOD 自身の抽選と `random` の内側は数えない（版3）。
RANDOM_SAMPLES = 1000

# 頼み文の関数。難易度は引数で受け取るので、**組み立てたのは呼び出し元**。
PROMPT_TARGETS = (
    "scripts.llm.llm_manager_world_generate:settlement_quest_generator",
    "scripts.llm.llm_manager_world_generate:random_quest_generator",
    "scripts.llm.llm_manager_world_generate:create_settlement_detail",
)

# `scripts.functions` の関数（画面用と文言用を除く全部）。移動の窓の間だけ写す。
# 1回目で変換2本が呼ばれなかったので、どれが呼ばれるかを全部で見る。
FUNCTION_NAMES = (
    "clamp_character_level", "clamp_equipment_value", "clamp_npc_difficulty_value",
    "clamp_quest_difficulty_value", "clamp_quest_scaling_value",
    "get_action_months", "get_active_quest_difficulties", "get_area_average_difficulty",
    "get_area_quest_difficulty_for_tier", "get_base_damage_value",
    "get_character_attributes", "get_days_elapsed_experience_point",
    "get_enemy_attributes_base_point", "get_enemy_count_in_quest", "get_enemy_exp_lvl",
    "get_equipment_level_from_price", "get_equipment_price",
    "get_heal_item_level_from_price", "get_heal_item_price",
    "get_heal_physical_integrity_barden", "get_heal_spec", "get_instant_damage",
    "get_item_base_price", "get_item_skill_usefulness", "get_labor_reward",
    "get_location_id_from_name", "get_location_instance_from_id",
    "get_max_physical_integrity", "get_node_instance_from_id", "get_npc_attributes",
    "get_npc_employ_price", "get_npc_exp_level", "get_other_item_level_from_price",
    "get_other_item_price", "get_quest_appropriate_stat", "get_quest_difficulties",
    "get_quest_difficulty_from_stat", "get_quest_reward", "get_randomized_item_price",
    "get_talent_point_by_quest_level", "get_talent_point_quest_clear",
    "get_talent_point_story_quest_clear", "get_training_experience_point",
    "get_training_price", "get_training_price_quest_clear", "get_weapon_spec",
    "training_efficiency_ratio",
)
PURE_TARGETS = tuple("scripts.functions:{}".format(name) for name in FUNCTION_NAMES)

# 街が作られる経路。
BUILD_TARGETS = (
    "__main__:Area.update",
    "__main__:Area.generate_nodes",
    "__main__:World.generate_quests",
    "__main__:World.generate_quest_area",
    "save_area_json:generate_quest_area",
    "save_area_json:write_area_data_to_world_dict",
)

# 到着の移動の間だけ写す乱数。`random` のモジュール関数と `Random` のメソッドの両方
# （モジュール関数は import 時に束縛済みの bound method なので、クラス側だけ包んでも届かない。
# 逆にゲームが `random.Random(seed)` を作って使う場合はクラス側でしか届かない）。
RANDOM_NAMES = ("betavariate", "binomialvariate", "choice", "choices", "expovariate",
                "gammavariate", "gauss", "lognormvariate", "normalvariate",
                "paretovariate", "randbytes", "randint", "random", "randrange", "sample",
                "shuffle", "triangular", "uniform", "vonmisesvariate", "weibullvariate")
# `getrandbits` と `Random.random` は他の乱数の内側で呼ばれる部品（`choice` 1回で
# `getrandbits` が1〜2回）。2回目の計測で上限300件のうち228件がこれで埋まり、
# 肝心の場面に届く前に打ち切られた。外側の呼び出しを写せば足りるので外す。
RANDOM_TARGETS = tuple("random:{}".format(name) for name in RANDOM_NAMES) \
    + tuple("random:Random.{}".format(name) for name in RANDOM_NAMES
            if name != "random") \
    + ("random:Random.seed", "random:Random.__init__")
# numpy が読まれていれば（画像生成で使う）そちらの乱数も。無ければ黙って降りる。
NUMPY_TARGETS = tuple("numpy.random:{}".format(name) for name in (
    "randint", "uniform", "choice", "random", "rand", "randn", "normal", "random_sample",
    "random_integers", "shuffle", "permutation"))

# 乱数の呼び出し元として数えないもの（画像生成の種と、`random` 自身の内側）。
RANDOM_IGNORE = ("stable_diffusion", "random.py")


def called_from_a_mod():
    """この乱数を呼んだのがローダか MOD 自身なら True。

    `frames.caller` はこちら側のフレームを飛ばしてゲーム側を返すので、
    `132_` が自前の `Random` から引いた種の抽選も「ゲームの `send_request_on_id` から」に見える
    （2回目の計測で 72 件がこれだった）。
    ここではラッパの層だけを飛ばし、その次のフレームが MOD のファイルなら数えない。
    """
    index = 1
    while index < 12:
        try:
            frame = sys._getframe(index)
        except Exception:
            return False
        index += 1
        name = frame.f_code.co_filename
        if not frames.is_ours(name):
            return False
        # この probe 自身のフレーム（`rng` ラッパ）とローダの層は飛ばす。
        # 3回目の計測はここを飛ばしていなかったので、自分のラッパを「MOD からの呼び出し」と
        # 見なして全件を捨てていた（`random_calls=0`）。
        if name == __file__ or "instantale_modloader" in name.replace("/", "\\"):
            continue
        return True
    return False


def apply(ctx):
    record_path = ctx.out_path(RECORD_BASENAME)
    write = ctx.logger(LOG_BASENAME)

    state = {"window": None, "random_seen": 0}

    def now():
        return datetime.datetime.now().isoformat(timespec="seconds")

    def record(row):
        try:
            with open(record_path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
        except Exception:
            ctx.log_exc("area quest difficulty probe: record failed")

    def brief(value, limit=80):
        """引数を短く写す。長文は先頭だけ、オブジェクトは id と名前だけ。"""
        if value is None or isinstance(value, (bool, int, float)):
            return value
        if isinstance(value, str):
            return frames.short(value, limit)
        if isinstance(value, (list, tuple)):
            return [brief(item, limit) for item in list(value)[:12]]
        if isinstance(value, dict):
            return {str(key): brief(value[key], limit) for key in list(value)[:12]}
        if isinstance(value, range):
            return "range({}, {}, {})".format(value.start, value.stop, value.step)
        return frames.short(frames.describe_instance(value), limit)

    def context(app):
        """その瞬間の世界の段階。難易度が物語の段階に連れて動くなら、ここで見分ける。"""
        if app is None:
            app = ui.find_app()
        world_dict = frames.attr(app, "world_dict", None)
        data = world_dict.get("world_data") if isinstance(world_dict, dict) else None
        story = data.get("story") if isinstance(data, dict) else None
        player = frames.attr(app, "player", None)
        area = ui.current_area(app)
        return {
            "story_phase": story.get("current_story_phase") if isinstance(story, dict) else None,
            "days": ui.game_day(app),
            "highest_cleared": frames.attr(app, "highest_cleared_quest_difficulty", None),
            "player_level": frames.attr(player, "experience_level", None),
            "current_area": {"id": ui.area_id_of(area),
                             "name": frames.attr(area, "name", None)},
        }

    def area_brief(app, area_id):
        """街1つの今の状態。`level_of_detail` と依頼の数が「もう作られたか」の答え。"""
        area = ui.world_areas(app).get(str(area_id)) if app is not None else None
        config = frames.attr(area, "config", None)
        quests = frames.attr(area, "quests", None)
        nodes = frames.attr(area, "nodes", None)
        raw = None
        world_dict = frames.attr(app, "world_dict", None)
        if isinstance(world_dict, dict):
            areas = world_dict.get("areas")
            raw = areas.get(str(area_id)) if isinstance(areas, dict) else None
        raw_config = raw.get("config") if isinstance(raw, dict) else None
        return {
            "id": str(area_id),
            "name": frames.attr(area, "name", None),
            "size": raw.get("size") if isinstance(raw, dict) else None,
            "level_of_detail": config.get("level_of_detail") if isinstance(config, dict) else None,
            "raw_level_of_detail": raw_config.get("level_of_detail")
            if isinstance(raw_config, dict) else None,
            "quests": brief(quests),
            "nodes": len(nodes) if isinstance(nodes, dict) else None,
            "difficulties": brief(ui_difficulties(app, area)),
        }

    def ui_difficulties(app, area):
        functions = sys.modules.get("scripts.functions")
        fn = getattr(functions, "get_quest_difficulties", None) if functions else None
        if fn is None or area is None:
            return None
        try:
            return list(fn(area, frames.attr(app, "world", None)))
        except Exception as exc:
            return "<{}>".format(type(exc).__name__)

    def log_row(row, line):
        record(row)
        write(line)

    def args_line(args, kwargs):
        return ", ".join([json.dumps(brief(a), ensure_ascii=False, default=str) for a in args]
                         + ["{}={}".format(k, json.dumps(brief(v), ensure_ascii=False,
                                                          default=str))
                            for k, v in kwargs.items()])

    # ------------------------------------------------------------ 頼み文の関数
    def watch_prompt(target):
        name = target.split(":")[-1]

        @ctx.wrap(target, required=False, safe=True)
        def prompt(orig, *args, **kwargs):
            try:
                row = {"at": now(), "phase": "頼み文", "func": name,
                       "args": [brief(a) for a in args],
                       "kwargs": {k: brief(v) for k, v in kwargs.items()},
                       "caller": frames.caller(depth=6, skip_hints=("pydantic",))}
                row.update(context(None))
                found = [a for a in list(args) + list(kwargs.values())
                         if isinstance(a, (list, tuple, int, float))
                         and not isinstance(a, bool)]
                log_row(row, "頼み文: {} difficulties={} caller={}".format(
                    name, found, row["caller"]))
            except Exception:
                ctx.log_exc("area quest difficulty probe: cannot record {}".format(name))
            return orig(*args, **kwargs)
        return prompt

    for target in PROMPT_TARGETS:
        watch_prompt(target)

    # ------------------------------------------------------------ scripts.functions
    def watch_pure(target):
        name = target.split(":")[-1]

        @ctx.wrap(target, required=False, safe=True)
        def pure(orig, *args, **kwargs):
            result = orig(*args, **kwargs)
            if state["window"] is None:
                return result
            try:
                log_row({"at": now(), "phase": "関数", "func": name,
                         "args": [brief(a) for a in args],
                         "kwargs": {k: brief(v) for k, v in kwargs.items()},
                         "result": brief(result), "caller": frames.caller(depth=4)},
                        "関数: {}({}) -> {!r} caller={}".format(
                            name, args_line(args, kwargs), brief(result),
                            frames.caller(depth=3)))
            except Exception:
                pass
            return result
        return pure

    for target in PURE_TARGETS:
        watch_pure(target)

    # ------------------------------------------------------------ 街が作られる経路
    def area_id_from(args, kwargs):
        """経路の関数の引数から街の id を拾う。名前が違っても値が str/int なら候補。"""
        for key in ("area_id", "next_area_id"):
            if key in kwargs:
                return str(kwargs[key])
        for value in args:
            if isinstance(value, (str, int)) and not isinstance(value, bool):
                return str(value)
            if isinstance(value, dict) and "id" in value:
                return str(value.get("id"))
        return None

    def watch_build(target):
        name = target.split(":")[-1]

        @ctx.wrap(target, required=False, safe=True)
        def build(orig, *args, **kwargs):
            app = ui.find_app()
            started = time.monotonic()
            area_id = None
            try:
                rest = list(args[1:]) if args and not isinstance(args[0], (str, int, dict)) \
                    else list(args)
                area_id = area_id_from(rest, kwargs)
                if area_id is None and args and not isinstance(args[0], (str, int, dict)):
                    area_id = frames.attr(args[0], "id", None)
                before = area_brief(app, area_id) if area_id is not None else None
                write("経路: {} 開始 area={} caller={}".format(
                    name, area_id, frames.caller(depth=4)))
            except Exception:
                before = None
            result = orig(*args, **kwargs)
            try:
                row = {"at": now(), "phase": "経路", "func": name,
                       "seconds": round(time.monotonic() - started, 1),
                       "args": [brief(a) for a in args],
                       "before": before,
                       "after": area_brief(app, area_id) if area_id is not None else None,
                       "caller": frames.caller(depth=5)}
                row.update(context(app))
                log_row(row, "経路: {} 終了 area={} {:.1f}s lod {} -> {} quests {} -> {}".format(
                    name, area_id, row["seconds"],
                    before.get("level_of_detail") if before else None,
                    row["after"].get("level_of_detail") if row["after"] else None,
                    before.get("quests") if before else None,
                    row["after"].get("quests") if row["after"] else None))
            except Exception:
                ctx.log_exc("area quest difficulty probe: cannot record {}".format(name))
            return result
        return build

    for target in BUILD_TARGETS:
        watch_build(target)

    # ------------------------------------------------------------ 到着の移動の窓
    @ctx.wrap("__main__:AreaMoveManager.__init__", required=False, safe=True)
    def move_init(orig, self, app, target_area_id, mode, *args, **kwargs):
        result = orig(self, app, target_area_id, mode, *args, **kwargs)
        try:
            self._probe_area_quest_difficulty = {"target_id": str(target_area_id),
                                                 "mode": str(mode)}
        except Exception:
            pass
        return result

    @ctx.wrap("__main__:AreaMoveManager.execute", required=False)
    def move_execute(orig, self, choice_text=None, *args, **kwargs):
        """移動の窓。到着先の街が作られるならこの中。前後の街の状態と、間の乱数・関数を写す。"""
        app = frames.attr(self, "app", None) or ui.find_app()
        info = frames.attr(self, "_probe_area_quest_difficulty", None) or {}
        target_id = info.get("target_id")
        window = {"target_id": target_id, "random": []}
        try:
            before = area_brief(app, target_id) if target_id else None
            write("=" * 78)
            write("移動: -> {} ({}) before={}".format(
                target_id, info.get("mode"), json.dumps(before, ensure_ascii=False,
                                                        default=str)))
            state["window"] = window
            state["random_seen"] = 0
        except Exception:
            ctx.log_exc("area quest difficulty probe: cannot open the move window")
            before = None
        try:
            return orig(self, choice_text, *args, **kwargs)
        finally:
            state["window"] = None
            try:
                row = {"at": now(), "phase": "移動", "target": target_id,
                       "mode": info.get("mode"), "before": before,
                       "after": area_brief(app, target_id) if target_id else None,
                       "random_calls": len(window["random"])}
                row.update(context(app))
                log_row(row, "移動: 到着 {} after={} random_calls={}".format(
                    target_id, json.dumps(row["after"], ensure_ascii=False, default=str),
                    len(window["random"])))
            except Exception:
                ctx.log_exc("area quest difficulty probe: cannot close the move window")

    # ------------------------------------------------------------ 乱数
    def watch_random(target):
        name = target.split(":")[-1]

        @ctx.wrap(target, required=False, safe=True)
        def rng(orig, *args, **kwargs):
            result = orig(*args, **kwargs)
            try:
                window = state["window"]
                if window is not None and RANDOM_SAMPLES > 0 \
                        and state["random_seen"] < RANDOM_SAMPLES \
                        and not called_from_a_mod():
                    caller = frames.caller(depth=3)
                    if caller and caller != "?" \
                            and not any(hint in caller.split(" <- ")[0]
                                        for hint in RANDOM_IGNORE):
                        state["random_seen"] += 1
                        window["random"].append(name)
                        # `Random.*` はメソッドなので第1引数が self。
                        # 見せるときは落とす（名前には `Random.` が既に付いている）。
                        shown = list(args)[1:] if "Random." in name else list(args)
                        log_row({"at": now(), "phase": "乱数", "func": name,
                                 "target": target,
                                 "args": [brief(a) for a in shown],
                                 "kwargs": {k: brief(v) for k, v in kwargs.items()},
                                 "result": brief(result), "caller": caller},
                                "乱数: {}({}) -> {!r} caller={}".format(
                                    name, args_line(shown, kwargs), brief(result), caller))
            except Exception:
                pass
            return result
        return rng

    for target in RANDOM_TARGETS + NUMPY_TARGETS:
        watch_random(target)

    ctx.log("area quest difficulty probe v2: {} function(s), {} random target(s), log={}".format(
        len(PURE_TARGETS), len(RANDOM_TARGETS) + len(NUMPY_TARGETS),
        ctx.out_path(LOG_BASENAME)))
