# -*- coding: utf-8 -*-
"""220_probe_bounty_hunter をゲーム抜きで通す。

    python tools/tests/test_bounty_hunter_probe.py

偽の `ui` と偽のゲーム関数を差し込み、次を確認する。

  数え方   … `wanted_summary` が手配された土地・いちばん重い土地・合計を出す。
             境界（手配度 0）と、数でない値（True / None）の扱い
  形だけ   … `shape` が中身ではなく形を出す。入れ子は打ち切られ、
             長い文字列は切り詰められ、鍵の数には上限がある
  変えない … 26の対象すべてで本体が1回だけ呼ばれ、戻り値がそのまま返る。
             引数も素通しする
  記録     … `out/bounty_hunter.jsonl` が1件1行の JSON で、
             戦闘の入口・衛兵・手配度の行が残る
  壊れても … 記録が失敗しても本体は呼ばれ、戻り値は変わらない
  経路     … 対象が全部登録される
"""
import importlib.util
import io
import json
import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
RUNTIME_DIR = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir, "runtime"))
MODS_DIR = os.path.join(RUNTIME_DIR, "mods")
OUT_DIR = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir, "out", "test"))

if RUNTIME_DIR not in sys.path:
    sys.path.insert(0, RUNTIME_DIR)

# 登録されるはずの対象。数だけでなく名前で見る（打ち間違えは `required=False`
# のせいで実機では黙って降りる）。
TARGETS = (
    "__main__:BattleStartManager.__init__",
    "__main__:BattleStartManager.start_battle",
    "__main__:InstantaleApp.execute_battle_process",
    "__main__:InstantaleApp.start_battle_with_in_conversation",
    "__main__:InstantaleApp.generate_character_from_enemy_data",
    "__main__:InstantaleApp.generate_enemy_instance_from_quest_dict",
    "scripts.llm.llm_manager:guard_npc_generator",
    "scripts.llm.llm_manager:guard_battle_summarizer",
    "scripts.functions:get_enemy_exp_lvl",
    "scripts.functions:get_enemy_attributes_base_point",
    "scripts.functions:get_enemy_count_in_quest",
    "__main__:AreaMoveManager.execute",
    "__main__:MovePhaseManager.move_phase",
    "__main__:InstantaleApp.elapse_days",
    "scripts.llm.llm_manager:master_ai_facilitator",
    "__main__:InstantaleApp.refresh_choice_buttons",
    "__main__:FreeInputStart.end_process",
    "__main__:FreeInputStart.end_process_in_conversation",
    "__main__:FreeInputStart.end_process_in_quest",
    "__main__:FreeInputStart.end_process_in_conversation_in_quest",
    "__main__:InstantaleApp.set_buttons_to_normal",
    "__main__:InstantaleApp.display_button_load",
    "scripts.hud.new_hud:InstanTaleHUD.update_button_texts",
    "scripts.hud.new_hud:InstanTaleHUD.update_enemy_display",
    "scripts.hud.new_hud:InstanTaleHUD.turnoff_window_visibility",
    "__main__:InstantaleApp.update_enemy_info",
)

RECORD_NAME = "bounty_hunter.jsonl"
LOG_NAME = "bounty_hunter.log"


def find_mod(suffix):
    """mod を **番号を除いた名前** で探す（番号は振り直されることがある）。"""
    matches = sorted(name for name in os.listdir(MODS_DIR)
                     if name.endswith(suffix)
                     and os.path.isfile(os.path.join(MODS_DIR, name, "mod.json")))
    if not matches:
        raise SystemExit("cannot find *{} in {}".format(suffix, MODS_DIR))
    if len(matches) > 1:
        raise SystemExit("ambiguous: {} in {}".format(matches, MODS_DIR))
    folder = os.path.join(MODS_DIR, matches[0])
    with io.open(os.path.join(folder, "mod.json"), encoding="utf-8") as fh:
        entry = json.load(fh)["entry"]
    return folder, os.path.join(folder, entry)


MOD_DIR, MOD = find_mod("_probe_bounty_hunter")

failures = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name
          + ((" -- " + str(detail)) if detail and not cond else ""))
    if not cond:
        failures.append(name)


# ---------------------------------------------------------------- 偽ゲーム
class Character(object):
    def __init__(self, name, level=1, hp=10):
        self.name = name
        self.experience_level = level
        self.current_hp = hp
        self.max_hp = hp


class Area(object):
    def __init__(self, area_id, name):
        self.id = area_id
        self.name = name


class App(object):
    """`app` の代わり。手配度は `player.area_history` に持つ（本物と同じ形）。"""

    def __init__(self, lawfulness, enemies=None, busy=()):
        self.player = types.SimpleNamespace(
            experience_level=24, current_hp=100, location="106",
            area_history={area_id: {"residency": {}, "achievements": [],
                                    "lawfulness": value}
                          for area_id, value in lawfulness.items()})
        self.current_enemy_dict = dict(enemies or {})
        self.busy = list(busy)
        # HUD の代わり。ウィジェットの目印は `pos` と `size` の両方を持つこと。
        self.hud = types.SimpleNamespace(
            right_button_layout=types.SimpleNamespace(
                pos=[10, 20], size=[300, 400], opacity=1.0, disabled=False,
                children=[]),
            top_info_layout_battle=types.SimpleNamespace(
                pos=[10, 20], size=[300, 400], opacity=1.0, disabled=False,
                children=[]),
            not_a_widget="text")


class FakeUI(object):
    """`instantale_modloader.ui` の代わり。**読み方は本物を借りる。**

    手配度の読み取りはローダの語彙（`ui.lawfulness_by_area`）なので、
    偽で置き換えずに本物を通す。ここで偽物を書くと、
    この計測が実際に使う読み方を検査しないことになる。
    """

    def __init__(self, app):
        import instantale_modloader.ui as real
        self._real = real
        self.app = app
        self.lawfulness_by_area = real.lawfulness_by_area
        self.busy_signals = lambda app: list(getattr(app, "busy", []))

    def find_app(self):
        return self.app

    def current_area(self, app):
        return Area("3", "陽光の砦")

    def area_id_of(self, area):
        return self._real.area_id_of(area)

    def facility_type_of(self, facility):
        return "inn"

    def spec_cls_name(self, entry):
        return self._real.spec_cls_name(entry)

    def find_hud(self, app):
        return getattr(app, "hud", None)


class FakeCtx(object):
    def __init__(self, out_dir):
        self.out_dir = out_dir
        self.hooks = {}
        self.errors = []
        self.logs = []

    def out_path(self, *parts):
        path = os.path.join(self.out_dir, *parts)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        return path

    # ログは本物の `ctx.logger` をそのまま借りる（`test_party_opponent` と同じ）。
    _mod = "220_probe_bounty_hunter"

    def logger(self, name, *, tag=None, stamp=True, label=None):
        import instantale_modloader as _ml
        return _ml.ModContext.logger(self, name, tag=tag, stamp=stamp, label=label)

    def log(self, msg, level="INFO"):
        self.logs.append((level, msg))

    def log_exc(self, msg):
        self.errors.append(msg)

    def wrap(self, target, **kw):
        def decorator(func):
            self.hooks[target] = func
            return func
        return decorator


def load_mod(name="bounty_hunter_probe_mod"):
    sys.modules.pop(name, None)
    spec = importlib.util.spec_from_file_location(
        name, MOD, submodule_search_locations=[MOD_DIR])
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def fresh_mod(app):
    """mod を読み直して当て直す。記録は毎回まっさらにする。"""
    for name in (RECORD_NAME, LOG_NAME):
        path = os.path.join(OUT_DIR, name)
        if os.path.exists(path):
            os.remove(path)
    module = load_mod()
    module.ui = FakeUI(app)
    ctx = FakeCtx(OUT_DIR)
    module.apply(ctx)
    return module, ctx


def read_records():
    path = os.path.join(OUT_DIR, RECORD_NAME)
    if not os.path.exists(path):
        return []
    with io.open(path, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def read_log():
    path = os.path.join(OUT_DIR, LOG_NAME)
    if not os.path.exists(path):
        return []
    with io.open(path, encoding="utf-8") as fh:
        return fh.read().splitlines()


def counting(result):
    """本体の代わり。呼ばれた引数を控える。"""
    calls = []

    def orig(*args, **kwargs):
        calls.append((args, kwargs))
        return result
    return orig, calls


# ------------------------------------------------------------------ 検査
def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    wanted = {"0": 10, "3": -12, "7": -30}

    print("数え方")
    module, ctx = fresh_mod(App(wanted))
    check("注入時の自己検証が通っている",
          not [level for level, _ in ctx.logs if level == "ERROR"], ctx.logs)
    summary = module.wanted_summary(wanted, 0)
    check("手配された土地だけを数える", summary["wanted_areas"] == 2, summary)
    check("いちばん重い土地とその重さが出る",
          (summary["worst"], summary["worst_area"]) == (30, "7"), summary)
    check("合計は負の側だけを足す（平常の土地は押し上げない）",
          summary["total"] == 42, summary)
    check("記録のある土地の数も出る", summary["areas"] == 3, summary)
    edge = module.wanted_summary({"0": 0, "1": 1}, 0)
    check("手配度 0 は手配とみなさない（309_ と同じ境界）",
          edge["wanted_areas"] == 0, edge)
    odd = module.wanted_summary({"0": True, "1": None, "2": "-5", "3": -2}, 0)
    check("数でない値は数えない（True を手配度 1 と読まない）",
          (odd["areas"], odd["total"]) == (1, 2), odd)
    raised = module.wanted_summary({"0": 5, "1": 10}, 10)
    check("閾値を上げれば平常の側も数える",
          (raised["wanted_areas"], raised["total"]) == (1, 5), raised)
    check("空でも落ちない", module.wanted_summary({}, 0)["total"] == 0)
    check("None でも落ちない", module.wanted_summary(None, 0)["areas"] == 0)

    print("形だけ")
    shape = module.shape
    body = shape({"description": "あ" * 500, "look": {"category": "human_male"}})
    check("辞書は鍵と件数を出す",
          body["keys"] == ["description", "look"] and body["len"] == 2, body)
    check("入れ子は1段だけ辿る",
          body["first"]["type"] == "str" and "first" not in body["first"], body)
    check("長い文字列は本文を出さず長さと頭だけ",
          body["first"]["len"] == 500 and len(body["first"]["head"]) <= 61,
          body["first"])
    listed = shape([{"a": 1}, {"b": 2}])
    check("配列は件数と先頭の形",
          (listed["len"], listed["first"]["keys"]) == (2, ["a"]), listed)
    wide = shape({str(n): n for n in range(100)})
    check("鍵の数には上限がある",
          len(wide["keys"]) == module.MAX_KEYS and wide["len"] == 100, wide)
    obj = shape(Character("賞金稼ぎ", 30, 240))
    check("オブジェクトは型と属性名",
          obj["type"] == "Character" and "experience_level" in obj["attrs"], obj)
    check("素の値はそのまま",
          shape(7) == {"type": "int", "value": 7}, shape(7))
    check("None も落ちない", shape(None)["value"] is None)

    print("変えない")
    app = App(wanted, enemies={"賞金稼ぎ1": Character("賞金稼ぎ", 30, 240)})
    module, ctx = fresh_mod(app)
    check("対象が全部登録される",
          sorted(ctx.hooks) == sorted(TARGETS),
          set(TARGETS) ^ set(ctx.hooks))
    for target in TARGETS:
        orig, calls = counting("戻り値")
        returned = ctx.hooks[target](orig, app, "A", "B")
        ok = returned == "戻り値" and len(calls) == 1
        if not ok:
            check("{} が素通しする".format(target), False, (returned, calls))
            break
    else:
        check("26の対象すべてで本体が1回だけ呼ばれ、戻り値がそのまま返る", True)
    orig, calls = counting(None)
    ctx.hooks["__main__:InstantaleApp.elapse_days"](orig, app, 14)
    check("引数も素通しする", calls == [((app, 14), {})], calls)
    check("記録に失敗していない", not ctx.errors, ctx.errors)

    print("記録")
    module, ctx = fresh_mod(app)
    orig, _ = counting(None)
    ctx.hooks["__main__:BattleStartManager.__init__"](
        orig, types.SimpleNamespace(app=app), app, "quest",
        {"description": "追手", "archetype": "balanced"})
    ctx.hooks["__main__:BattleStartManager.start_battle"](
        orig, types.SimpleNamespace(app=app))
    ctx.hooks["scripts.llm.llm_manager:guard_npc_generator"](
        orig, Area("3", "陽光の砦"), None, 24)
    ctx.hooks["__main__:AreaMoveManager.execute"](
        orig, types.SimpleNamespace(app=app), "馬車(1000G)")
    rows = read_records()
    phases = [row.get("phase") for row in rows]
    check("戦闘の入口が1件1行で残る",
          "BattleStartManager.__init__" in phases, phases)
    check("enemy_type の実値がそのまま残る（語彙を知るための1行）",
          any(row.get("enemy_type", {}).get("head") == "quest" for row in rows),
          rows[:1])
    check("始まった後の敵も残る",
          any(row.get("enemies") for row in rows if row["phase"] == "start_battle"),
          phases)
    check("衛兵の難易度と呼び出し元が残る",
          any(row["phase"] == "guard_npc_generator"
              and row["npc_difficulty_level"]["value"] == 24
              and "caller" in row for row in rows), phases)
    arrival = [row for row in rows if row["phase"] == "到着(土地)"]
    check("到着の行に手配度が土地ごとと要約の両方で残る",
          arrival and arrival[0]["lawfulness"] == {"0": 10, "3": -12, "7": -30}
          and arrival[0]["wanted"]["total"] == 42, arrival)
    check("手が空いているかも残る", arrival and arrival[0]["busy"] == [], arrival)
    check("読む用のログにも1行ずつ出る", len(read_log()) >= 4, read_log())

    print("画面が戻る合図")
    module, ctx = fresh_mod(app)
    orig, calls = counting("戻り値")
    returned = ctx.hooks["__main__:FreeInputStart.end_process"](orig, app, "本文")
    check("自由入力の出口を素通しして記録する",
          returned == "戻り値" and len(calls) == 1
          and any(r["phase"] == "自由入力の出口" for r in read_records()),
          (returned, calls))
    orig, calls = counting(None)
    ctx.hooks["__main__:InstantaleApp.refresh_choice_buttons"](orig, app)
    rows = [r for r in read_records() if r["phase"] == "画面が戻った"]
    check("合図に、直前の出口からの秒数が入る",
          rows and rows[0]["after"] == "end_process"
          and isinstance(rows[0]["since_free_input"], (int, float)), rows)
    check("そのとき並んでいる選択肢も残る",
          rows and "buttons" in rows[0], rows)
    module, ctx = fresh_mod(app)
    module.SCREEN_SAMPLES = 0
    orig, calls = counting(None)
    ctx.hooks["__main__:InstantaleApp.refresh_choice_buttons"](orig, app)
    check("0 にすれば合図は録らない（本体は呼ばれる）",
          not [r for r in read_records() if r["phase"] == "画面が戻った"]
          and len(calls) == 1, read_records())

    print("戦闘に入ったときの配置")
    module, ctx = fresh_mod(app)
    orig, _ = counting(None)
    ctx.hooks["__main__:BattleStartManager.__init__"](
        orig, types.SimpleNamespace(app=app), app, "guard", None)
    ctx.hooks["__main__:BattleStartManager.start_battle"](
        orig, types.SimpleNamespace(app=app))
    rows = [r for r in read_records() if r["phase"] == "start_battle"]
    check("HUD の枠の実寸が残る",
          rows and "right_button_layout" in (rows[-1].get("hud") or {}),
          rows[-1].get("hud") if rows else None)
    check("ウィジェットでない属性は写さない",
          rows and "not_a_widget" not in (rows[-1].get("hud") or {}),
          rows[-1].get("hud") if rows else None)
    check("位置・大きさ・濃さが揃っている",
          rows and set(rows[-1]["hud"]["top_info_layout_battle"]) >= {"pos", "size", "opacity"},
          rows[-1]["hud"]["top_info_layout_battle"] if rows else None)
    check("誰が起こしたかが残る",
          rows and rows[-1]["started_by"] in ("ゲーム(button)", "MOD/その他"),
          rows[-1].get("started_by") if rows else None)

    print("枠の出し入れ")
    app2 = App({"0": -5})
    module, ctx = fresh_mod(app2)
    hud = app2.hud

    def collapse(*args, **kwargs):
        hud.top_info_layout_battle.size = [0, 0]
        hud.top_info_layout_battle.opacity = 0
        return "戻り値"

    returned = ctx.hooks["scripts.hud.new_hud:InstanTaleHUD.update_enemy_display"](
        collapse, hud)
    rows = [r for r in read_records() if r["phase"] == "枠が動いた"]
    check("枠が動いたら前後が残る",
          rows and rows[-1]["before"] != rows[-1]["after"], rows[-1] if rows else None)
    check("その段も素通し（戻り値そのまま）", returned == "戻り値", returned)
    orig, calls = counting("戻り値")
    ctx.hooks["scripts.hud.new_hud:InstanTaleHUD.update_enemy_display"](orig, hud)
    check("動かなければ書かない",
          len([r for r in read_records() if r["phase"] == "枠が動いた"]) == 1
          and len(calls) == 1, read_records())

    print("強さの表")
    module, ctx = fresh_mod(app)
    target = "scripts.functions:get_enemy_exp_lvl"
    for _ in range(3):
        orig, _ = counting(12)
        ctx.hooks[target](orig, 2, 5)
    orig, _ = counting(20)
    ctx.hooks[target](orig, 3, 5)
    rows = [row for row in read_records() if row["phase"] == "strength"]
    check("同じ引数の組は1度しか書かない", len(rows) == 2, rows)
    check("引数と戻り値が対で残る",
          rows[0]["args"][0]["value"] == 2 and rows[0]["result"]["value"] == 12,
          rows[0])
    module, ctx = fresh_mod(app)
    module.SCALING_SAMPLES = 0
    orig, calls = counting(12)
    ctx.hooks[target](orig, 2, 5)
    check("0 にすれば録らない（本体は呼ばれる）",
          not [r for r in read_records() if r["phase"] == "strength"]
          and len(calls) == 1, read_records())

    print("壊れても")
    module, ctx = fresh_mod(app)
    module.shape = None                      # 記録の途中で必ず落ちる形にする
    orig, calls = counting("戻り値")
    returned = ctx.hooks["__main__:InstantaleApp.execute_battle_process"](
        orig, app, {"敵1": object()})
    check("記録が壊れても本体は1回呼ばれ、戻り値は変わらない",
          returned == "戻り値" and len(calls) == 1, (returned, calls))
    check("壊れたことは握り潰さず記録に残す", ctx.errors, ctx.errors)

    print("読み方の出どころ")
    import instantale_modloader.ui as real_ui
    check("手配度の読み取りはローダの語彙を通っている",
          callable(getattr(real_ui, "lawfulness_by_area", None))
          and real_ui.lawfulness_by_area(App({"0": -3}).player) == {"0": -3})

    print()
    if failures:
        print("失敗 {} 件".format(len(failures)))
        for name in failures:
            print("  - " + name)
        return 1
    print("すべて通った")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
