# -*- coding: utf-8 -*-
"""319_battle_tactics の数の芯をゲーム抜きで通す。

    python tools/tests/test_battle_tactics.py

主にモジュール直下の純関数を叩く（フックの側は実機で確かめる。
VERIFICATION.md の 3xx の表）。フック越しに通すのは、後始末と型が崩れる経路だけ。

  帯       … power の列挙 → 火力に乗せる割合。知らない語は normal 扱い
  軽減     … 防御の飽和曲線と上限 60%
  1発      … 錨は攻め手の火力。雑魚と同格に同じ帯の数字が出て、
             手数は相手の体力から生まれる（雑魚2手 / 同格7手前後）
  復元     … 審判の生の戻り（辞書でもオブジェクトでも）から
             text_status の毎ターン効果と AttributeEffect を抜き出せる
  倍率     … AttributeEffect が (状態異常名, 説明, 倍率の帳簿) になる
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

if RUNTIME_DIR not in sys.path:
    sys.path.insert(0, RUNTIME_DIR)


def find_mod(suffix):
    matches = sorted(name for name in os.listdir(MODS_DIR)
                     if name.endswith(suffix)
                     and os.path.isfile(os.path.join(MODS_DIR, name, "mod.json")))
    if len(matches) != 1:
        raise SystemExit("cannot pin *{}: {}".format(suffix, matches))
    folder = os.path.join(MODS_DIR, matches[0])
    with io.open(os.path.join(folder, "mod.json"), encoding="utf-8") as fh:
        entry = json.load(fh)["entry"]
    return os.path.join(folder, entry)


def load_module(path):
    spec = importlib.util.spec_from_file_location("battle_tactics_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


mod = load_module(find_mod("_battle_tactics"))

failures = []


def check(label, ok, detail=""):
    if ok:
        print("  ok    {}".format(label))
    else:
        failures.append(label)
        print("  FAIL  {} {}".format(label, detail))


# ---------------------------------------------------------------- 帯
check("band: weak", mod.band_of("weak") == mod.BAND_WEAK / 100.0)
check("band: extreme", mod.band_of("extreme") == mod.BAND_EXTREME / 100.0)
check("band: unknown word falls to normal",
      mod.band_of("colossal") == mod.BAND_NORMAL / 100.0)

# ---------------------------------------------------------------- 軽減
check("mitigation: zero defense", mod.mitigation(0) == 0.0)
check("mitigation: capped", mod.mitigation(10 ** 9) == mod.MITIGATION_MAX)
check("mitigation: bad input", mod.mitigation("armor") == 0.0)

# ---------------------------------------------------------------- レベル差
check("level: within the fair gap nothing changes",
      mod.level_multiplier(0) == 1.0 and mod.level_multiplier(10) == 1.0
      and mod.level_multiplier(-10) == 1.0)
check("level: the elite gap doubles",
      mod.level_multiplier(15) == 2.0 and mod.level_multiplier(-15) == 0.5)
check("level: the outclass gap triples and stays there",
      mod.level_multiplier(20) == 3.0 and mod.level_multiplier(40) == 3.0)
check("level: ramps between the steps",
      1.0 < mod.level_multiplier(12) < 2.0
      and 2.0 < mod.level_multiplier(17) < 3.0)
check("level: unreadable levels count as fair",
      mod.level_gap(None, 30) == 0 and mod.level_gap("x", None) == 0)

# ---------------------------------------------------------------- 1発（実測の帯）
# エリス: 基礎値 896・Lv62・HP1560。数字は攻め手に固有で、
# 「大きい相手ほど大きい数字」は出ない。手数は相手の体力から生まれる。
mob = mod.hit_damage([("normal", 1)], 896, 460, 96,
                     attacker_level=62, defender_level=53)
equal = mod.hit_damage([("normal", 1)], 896, 1560, 150,
                       attacker_level=62, defender_level=62)
check("hit: the same swing lands in the same band on mob and equal",
      220 <= mob <= 260 and 200 <= equal <= 245
      and abs(mob - equal) < 0.2 * max(mob, equal),
      "mob={} equal={}".format(mob, equal))
check("hit: a mob falls in about 3 blows", 2 <= -(-460 // mob) <= 4,
      "hits={}".format(-(-460 // mob)))
check("hit: an equal takes 7-9 blows", 7 <= -(-1560 // equal) <= 9,
      "hits={}".format(-(-1560 // equal)))
boss = mod.hit_damage([("normal", 1)], 896, 3120, 300,
                      attacker_level=62, defender_level=62)
check("hit: a big boss simply lasts longer", 15 <= -(-3120 // boss) <= 24,
      "hits={}".format(-(-3120 // boss)))
check("hit: never below the floor",
      mod.hit_damage([("weak", 0.01)], 10, 10 ** 6, 10 ** 6) == 10 ** 4)

# 一撃と過剰殺傷: 数字は普段と同じ物差しのまま、相手の体力を超える。
slime = mod.hit_damage([("normal", 1)], 896, 64, 0,
                       attacker_level=62, defender_level=38)
check("hit: the hero one-shots a slime with an everyday-scale number",
      slime > 64 and 400 <= slime <= 900, "final={}".format(slime))
# 上限は無い（版12で撤廃）。互角への1発は式の時点で体力の一部に収まる。
equal_extreme = mod.hit_damage([("extreme", 1)], 896, 1560, 150,
                               attacker_level=62, defender_level=62)
check("hit: an equal's finisher hurts but does not kill from full",
      0.35 * 1560 <= equal_extreme <= 0.75 * 1560,
      "final={}".format(equal_extreme))
nuke = mod.hit_damage([("extreme", 1)], 1500 * 0.5, 1560, 500,
                      attacker_level=77, defender_level=62)
check("hit: an elite boss nuke is brutal but survivable from full",
      0.4 * 1560 <= nuke < 1560, "final={}".format(nuke))
guarded = mod.hit_damage([("extreme", 1)], 1500 * 0.5, 1560, 500, in_mult=0.5,
                         attacker_level=77, defender_level=62)
check("hit: guarding halves what gets through",
      abs(guarded - nuke * 0.5) <= 1, "nuke={} guarded={}".format(nuke, guarded))
demon = mod.hit_damage([("extreme", 1)], 5000, 300, 50,
                       attacker_level=80, defender_level=60)
check("hit: an outclassing boss fells a novice in one blow", demon > 300,
      "final={}".format(demon))

# 武器: 錨が基礎値なので √武器 がそのまま与ダメに乗る。
with_sword = mod.hit_damage([("normal", 1)], 896, 460, 96)
with_knife = mod.hit_damage([("normal", 1)], 189, 460, 96)
check("gear: the full weapon range multiplies damage by about 4.7",
      4.2 <= with_sword / with_knife <= 5.2,
      "ratio={}".format(with_sword / with_knife))

# 仲間: 錨は本人の max_hp（呼び出し側が受け側 max_hp で底上げする）。
weak_ally = mod.hit_damage([("weak", 1)], max(132, 460), 460, 96,
                           attacker_level=36, defender_level=36)
check("ally floor: a low-HP member still bites",
      weak_ally >= 460 * 0.10, "final={}".format(weak_ally))

# ---------------------------------------------------------------- 揺らぎ
import random as _random                                       # noqa: E402

_rng = _random.Random(20260827)
_rolls = [mod.damage_wobble(_rng) for _ in range(2000)]
check("wobble: stays inside the configured band",
      all(1 - mod.DAMAGE_WOBBLE / 100.0 <= r <= 1 + mod.DAMAGE_WOBBLE / 100.0
          for r in _rolls))
check("wobble: actually varies and centres on 1.0",
      len(set(_rolls)) > 1900 and abs(sum(_rolls) / len(_rolls) - 1.0) < 0.01,
      "mean={}".format(sum(_rolls) / len(_rolls)))
_saved = mod.DAMAGE_WOBBLE
mod.DAMAGE_WOBBLE = 0
check("wobble: zero means identical numbers every time",
      mod.damage_wobble(_rng) == 1.0)
mod.DAMAGE_WOBBLE = _saved

_low = mod.hit_damage([("normal", 1)], 896, 1560, 150, wobble=0.9)
_high = mod.hit_damage([("normal", 1)], 896, 1560, 150, wobble=1.1)
check("wobble: a swing swings the final number",
      _high > _low and abs(_high / _low - 1.22) < 0.05,
      "low={} high={}".format(_low, _high))

# ---------------------------------------------------------------- 出どころの受け渡し
from instantale_modloader import ui as _ui                     # noqa: E402

_ui.note_damage("エリス", 29, "泥の浸食")
check("notes: an exact match returns the label and is consumed",
      _ui.take_damage_notes("エリス", 29) == "泥の浸食"
      and _ui.take_damage_notes("エリス", 29) is None)
_ui.note_damage("エリス", 29, "泥の浸食")
_ui.note_damage("エリス", 46, "燃焼")
check("notes: a summed report joins the labels",
      _ui.take_damage_notes("エリス", 75) == "泥の浸食・燃焼")
_ui.note_damage("エリス", 29, "泥の浸食")
check("notes: a mismatch takes nothing",
      _ui.take_damage_notes("エリス", 100) is None
      and _ui.take_damage_notes("カイ", 29) is None
      and _ui.take_damage_notes("エリス", 29) == "泥の浸食")

# ---------------------------------------------------------------- 毎ターン
check("per-turn: intensity 3 is the baseline",
      mod.per_turn_amount(1000, "weak", 3) == 30)
check("per-turn: intensity stretches 2/3 to 4/3",
      (mod.per_turn_amount(1000, "weak", 1), mod.per_turn_amount(1000, "weak", 5))
      == (18, 42))
check("per-turn: at least 1", mod.per_turn_amount(3, "weak", 1) == 1)

# ---------------------------------------------------------------- 復元
sample = {
    "narration": "...",
    "additional_effects": [
        {"type": "text_status", "target": ["エリス"], "status_name": "泥濘の拘束",
         "description": "...", "duration": 3, "intensity": 2,
         "effects_per_turn": [
             {"type": "instant_damage", "target": ["エリス"], "power": "weak"}]},
        {"type": "reduction", "target": ["エリス"], "attribute_type": "str",
         "power": "strong"},
        {"type": "instant_heal", "target": ["エリス"], "power": "weak"},
    ],
}
extras = mod.extract_extras(sample)
check("extract: two effects survive, the plain heal does not", len(extras) == 2,
      repr(extras))
check("extract: the status keeps its per-turn recipe",
      extras[0] == {"kind": "status", "targets": ["エリス"],
                    "status_name": "泥濘の拘束",
                    "per_turn": [("instant_damage", "weak")],
                    "intensity": 2, "duration": 3}, repr(extras[0]))
check("extract: the attribute effect keeps its shape",
      extras[1] == {"kind": "attribute", "targets": ["エリス"],
                    "type": "reduction", "attribute_type": "str",
                    "power": "strong"}, repr(extras[1]))

# pydantic のモデルを模したオブジェクトでも同じに読めること。
as_objects = types.SimpleNamespace(
    additional_effects=[types.SimpleNamespace(
        type="text_status", target=["灰の王"], status_name="燃焼",
        description="...", duration=4, intensity=5,
        effects_per_turn=[types.SimpleNamespace(
            type="instant_damage", target=["灰の王"], power="strong")])])
extras = mod.extract_extras(as_objects)
check("extract: object form reads the same",
      extras and extras[0]["per_turn"] == [("instant_damage", "strong")]
      and extras[0]["intensity"] == 5, repr(extras))

check("extract: garbage yields nothing",
      mod.extract_extras(None) == [] and mod.extract_extras({"additional_effects": 3}) == [])

check("per-turn: description says the size, not the number",
      mod.per_turn_description([("instant_damage", "weak")]) == "毎ターン小ダメージ"
      and mod.per_turn_description([("instant_damage", "strong"),
                                    ("instant_heal", "weak")])
      == "毎ターン大ダメージ・毎ターン小回復")

# ---------------------------------------------------------------- 倍率
name, description, book = mod.attribute_recipe("reduction", "str", "strong")
check("attribute: str reduction lowers outgoing damage",
      name == "筋力低下" and abs(book["out_mult"] - 0.80) < 1e-9, repr(book))
name, description, book = mod.attribute_recipe("enhancement", "con", "weak")
check("attribute: con enhancement lowers incoming damage",
      name == "耐久強化" and abs(book["in_mult"] - 0.90) < 1e-9, repr(book))
check("attribute: unknown attributes are skipped",
      mod.attribute_recipe("enhancement", "luck", "weak") is None)

# 仲間の錨: 武器は上乗せにしかならない。防具は本体の値と装備の大きいほう
check("ally anchor: no weapon keeps the max_hp floor",
      mod.ally_anchor(460, 1560) == 1560 and mod.ally_anchor(460, 300) == 460)
check("ally anchor: a weapon adds 2*sqrt(ability*weapon) at the rate",
      abs(mod.ally_anchor(992, 600, 260, 323, percent=50) - (992 + 2 * (260 * 323) ** 0.5 * 0.5)) < 1e-9)
check("ally anchor: the rate comes from the setting by default",
      abs(mod.ally_anchor(992, 600, 260, 323) - mod.ally_anchor(992, 600, 260, 323, percent=mod.ALLY_GEAR_PERCENT)) < 1e-9)
check("ally anchor: a weak weapon still only adds",
      mod.ally_anchor(460, 600, 300, 23) > 600)
check("ally anchor: rate 0 and broken inputs fall back",
      mod.ally_anchor(460, 600, 300, 480, percent=0) == 600
      and mod.ally_anchor(460, 600, None, 480) == 600 and mod.ally_anchor(460, 600, "x", 480) == 600)
check("gear defense: the armor is added at the rate",
      mod.gear_defense(260, 268, percent=50) == 260 + 134 and mod.gear_defense(294, 100, percent=100) == 394
      and mod.gear_defense(294, None) == 294 and mod.gear_defense(294, 0) == 294
      and mod.gear_defense(294, 500, percent=0) == 294)

# ---------------------------------------------------------------- 揺らぎの乱数
# 省いた rng はこの MOD 専用の乱数から引く（ゲーム自身の乱数列をずらさない）。
_state = _random.getstate()
mod.damage_wobble()
check("wobble: the default draw leaves the global random untouched",
      _random.getstate() == _state)
check("wobble: the mod keeps its own Random",
      isinstance(mod._RNG, _random.Random) and mod._RNG is not _random)

# ---------------------------------------------------------------- フック
# 数の芯とは別に、後始末と型が崩れる経路だけをフック越しに通す。
import instantale_modloader as _ml                             # noqa: E402

OUT_DIR = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir, "out", "test"))


class _Ctx(object):
    """`apply()` を通すだけの ctx。ログの書き方は本物を借りる。"""
    _mod = "319_battle_tactics"

    def __init__(self):
        self.hooks = {}
        self.errors = []
        self.logs = []

    def out_path(self, *parts):
        path = os.path.join(OUT_DIR, *parts)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        return path

    def logger(self, name, **kw):
        return _ml.ModContext.logger(self, name, **kw)

    def warner(self, tag):
        return _ml.ModContext.warner(self, tag)

    def log(self, msg, level="INFO"):
        self.logs.append((level, msg))

    def log_exc(self, msg):
        self.errors.append(msg)

    def wrap(self, target, **kw):
        def decorator(func):
            self.hooks[target] = func
            return func
        return decorator


def _fresh():
    path = os.path.join(OUT_DIR, mod.LOG_BASENAME)
    if os.path.exists(path):
        os.remove(path)
    ctx = _Ctx()
    mod.apply(ctx)
    return ctx


def _log():
    path = os.path.join(OUT_DIR, mod.LOG_BASENAME)
    if not os.path.exists(path):
        return ""
    with io.open(path, encoding="utf-8") as fh:
        return fh.read()


# 継続回復が上限に当たった回。`max_hp_of` は float を返す。
_ctx = _fresh()
_king = types.SimpleNamespace(name="灰の王", max_hp=1560, current_hp=1550,
                              experience_level=60, status={"再生": {}})
_app = types.SimpleNamespace(current_enemy_dict={"灰の王": _king}, player=None)
_phase = types.SimpleNamespace(app=_app)
_ctx.hooks["__main__:BattlePhaseManager.convert_llm_output_to_instruction_dict"](
    lambda *a, **k: {}, _phase, None, None,
    {"additional_effects": [
        {"type": "text_status", "target": ["灰の王"], "status_name": "再生",
         "description": "...", "duration": 3, "intensity": 3,
         "effects_per_turn": [{"type": "instant_heal", "target": ["灰の王"],
                               "power": "weak"}]}]})
_ctx.hooks["__main__:BattlePhaseManager.reduce_status_turns_and_log"](
    lambda *a, **k: None, _phase, _king)
check("per-turn: a heal that hits the ceiling stays an int",
      _king.current_hp == 1560 and type(_king.current_hp) is int,
      repr(_king.current_hp))
check("per-turn: nothing was swallowed", not _ctx.errors, _ctx.errors)

# 1手の本体が投げても、その手は閉じる（構えが武装される）。
_ctx = _fresh()
_hero = types.SimpleNamespace(name="エリス", max_hp=1560, current_hp=1560,
                              experience_level=62, status={})
_app = types.SimpleNamespace(current_enemy_dict={}, player=_hero,
                             add_text=lambda text: None)
_saved_find_app = _ui.find_app
_ui.find_app = lambda: _app
try:
    def _raising_turn(*args, **kwargs):
        _ctx.hooks["scripts.llm.llm_manager_battle:"
                   "referee_player_any_input_new_new"](
            lambda *a, **k: None, None, _hero, "盾を構えて守る")
        raise RuntimeError("the game's turn failed")

    try:
        _ctx.hooks["__main__:BattlePhaseManager.handle_battle_situation"](
            _raising_turn, types.SimpleNamespace(app=_app), "エリス", "味方側",
            None)
        _raised = False
    except RuntimeError:
        _raised = True
finally:
    _ui.find_app = _saved_find_app
check("close: the game's exception still reaches the caller", _raised)
check("close: the action is closed and the guard armed",
      "guard armed: エリス" in _log(), _log())

print()
if failures:
    print("FAILED: {}".format(", ".join(failures)))
    raise SystemExit(1)
print("all ok")
