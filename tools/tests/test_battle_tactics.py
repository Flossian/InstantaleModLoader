# -*- coding: utf-8 -*-
"""319_battle_tactics の数の芯をゲーム抜きで通す。

    python tools/tests/test_battle_tactics.py

主にモジュール直下の純関数を叩く（フックの側は実機で確かめる。
VERIFICATION.md の 3xx の表）。フック越しに通すのは、後始末と型が崩れる経路と、
敵の体力の伸ばし方・1発の置き換えの通し。

  係数     … power の列挙 → ゲームの素点の係数 k。知らない語は normal 扱い
  1発      … 素点 × 素点 ÷ (素点 + 防御) × multiplier。同じ相手の2本目は k で割り戻して組む。
             Lv20 の実測の式で、雑魚は主人公の通常攻撃3発、敵の weak は HP の 3% 前後
  レベル差 … 格上の側の与ダメだけ伸びる（×1.5 / ×2）
  敵の体力 … ゲームの式どおりの HP だけ伸ばし、二度掛けしない
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


# ---------------------------------------------------------------- 係数
check("k: weak and extreme follow the game's table",
      mod.game_k("weak") == 1.0 and mod.game_k("extreme") == 2.45)
check("k: unknown word falls to normal", mod.game_k("colossal") == mod.GAME_K["normal"])

# ---------------------------------------------------------------- レベル差
check("level: within the fair gap nothing changes",
      mod.level_multiplier(0) == 1.0 and mod.level_multiplier(10) == 1.0)
check("level: the elite gap is x1.5 and the outclass gap x2, and stays there",
      mod.level_multiplier(15) == 1.5 and mod.level_multiplier(20) == 2.0
      and mod.level_multiplier(40) == 2.0)
check("level: ramps between the steps",
      1.0 < mod.level_multiplier(12) < 1.5 and 1.5 < mod.level_multiplier(17) < 2.0)
check("level: the lower side's damage is not divided",
      mod.level_multiplier(-15) == 1.0 and mod.level_multiplier(-40) == 1.0)
check("level: unreadable levels count as fair",
      mod.level_gap(None, 30) == 0 and mod.level_gap("x", None) == 0)

# ---------------------------------------------------------------- 1発
check("hit: defense equal to the raw lets half through",
      mod.hit_damage([("weak", 1)], 200, 200, 10 ** 4) == 100)
check("hit: no defense lets the raw through",
      mod.hit_damage([("weak", 1)], 200, 0, 10 ** 4) == 200)
check("hit: the referee's multiplier scales the blow (the game ignores it)",
      mod.hit_damage([("weak", 2)], 200, 200, 10 ** 4) == 200
      and mod.hit_damage([("normal", 0.67)], 240, 0, 10 ** 4) == 161)
# 同じ相手の2本目。ゲームは最後の裁き（extreme）の素点しか渡さない（実測 1185 → 2613〜3127）
check("hit: two rulings on one target are rebuilt from the last one's raw",
      mod.hit_damage([("weak", 2), ("extreme", 1)], 2450, 0, 10 ** 5) == 2000 + 2450)
check("hit: an enemy blow is scaled by the enemy setting",
      mod.hit_damage([("weak", 1)], 1000, 0, 10 ** 3, enemy=True)
      == round(1000 * mod.ENEMY_DAMAGE / 100.0))
check("hit: the higher side gets the level multiplier",
      mod.hit_damage([("weak", 1)], 200, 0, 10 ** 4, attacker_level=75, defender_level=60) == 300
      and mod.hit_damage([("weak", 1)], 200, 0, 10 ** 4, attacker_level=60, defender_level=75) == 200)
check("hit: never below the floor",
      mod.hit_damage([("weak", 0.01)], 10, 10 ** 6, 10 ** 6) == 10 ** 4)
check("hit: broken inputs do not raise",
      mod.hit_damage([], None, "x", None) == 1
      and mod.hit_damage([("weak", "x")], 100, 0, 0) == 100)
check("hit: a weapon bonus joins the base before k",
      mod.hit_damage([("strong", 1)], 150, 0, 10 ** 4, bonus=50) == 225)
_low = mod.hit_damage([("weak", 1)], 200, 100, 10 ** 3, in_mult=0.5)
check("hit: guarding halves what gets through",
      _low == round(200 * 200 / 300.0 * 0.5), "guarded={}".format(_low))

# Lv20 の実測の式（GAME.md §2.10.4）。主人公は筋力16・耐久15、武器と防具は価値20（攻撃力・防御力 89）。
# 雑魚は基準点 11.37 の balanced。通常攻撃は weak×1.5（VERIFICATION.md §3.77 の試算と同じ置き方）
_m = (20 + 5) / 5.0
_hero_raw = 2 * (16 * _m * 89) ** 0.5
_mob_hp = mod.scaled_hp(round(11.37 * 4 * _m), 11.37, 20)
_blow = mod.hit_damage([("weak", 1.5)], _hero_raw, 11.37 * _m, _mob_hp,
                       attacker_level=20, defender_level=20)
# 素点の揺らぎ（±5%）を除いた値で 3.0 発ぶん。揺らぎ込みでは3発か4発
check("hit: a Lv20 mob falls to about three basic attacks",
      _mob_hp and 2.8 <= _mob_hp / float(_blow) <= 3.2, "blow={} hp={}".format(_blow, _mob_hp))
_taken = mod.hit_damage([("weak", 1)], 2 * 11.37 * _m, 89, 15 * 4 * _m, enemy=True,
                        attacker_level=20, defender_level=20)
check("hit: a Lv20 mob's weak blow takes about 3% of the hero's HP",
      0.02 <= _taken / (15 * 4 * _m) <= 0.04, "taken={}".format(_taken))

# ---------------------------------------------------------------- 敵の体力
check("enemy hp: the game's own HP is stretched",
      mod.scaled_hp(760, 12, 74) == 1900)
check("enemy hp: an already stretched HP is left alone",
      mod.scaled_hp(1900, 12, 74) is None)
check("enemy hp: 100% and unreadable values change nothing",
      mod.scaled_hp(760, 12, 74, percent=100) is None
      and mod.scaled_hp(760, None, 74) is None and mod.scaled_hp("x", 12, 74) is None
      and mod.scaled_hp(760, 12, None) is None)

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

# スキル側の強化・弱体（変換が捨てる）。effect_id は 1 始まり、期限はスキルの duration
_skills = {"強者への追従": {"effects": [{"type": "buff", "target_type": "self", "attribute_type": "dex",
                                          "power": "strong", "duration": 2}]},
           "砂塵の足掻き": {"effects": [{"type": "debuff", "target_type": "single_enemy",
                                          "attribute_type": "dex", "power": "normal"}]},
           "放浪の連撃": {"effects": [{"type": "instant_damage", "power": "strong"}]}}
_buff = mod.skill_extras(_skills, {"skill": "強者への追従",
                                   "skill_effects": [{"effect_id": 1, "targets": ["メイ"]}]})
check("skill extras: a buff becomes an enhancement with the skill's duration",
      _buff == [{"kind": "attribute", "targets": ["メイ"], "type": "enhancement",
                 "attribute_type": "dex", "power": "strong", "duration": 2}], repr(_buff))
_debuff = mod.skill_extras(_skills, types.SimpleNamespace(
    skill="砂塵の足掻き", skill_effects=[types.SimpleNamespace(effect_id=1, targets="霧の声")]))
check("skill extras: a debuff becomes a reduction; no duration falls to the default",
      _debuff and _debuff[0]["type"] == "reduction" and _debuff[0]["targets"] == ["霧の声"]
      and _debuff[0]["duration"] is None, repr(_debuff))
check("skill extras: damage skills, unknown skills and bad effect ids yield nothing",
      mod.skill_extras(_skills, {"skill": "放浪の連撃", "skill_effects": [{"effect_id": 1, "targets": ["x"]}]}) == []
      and mod.skill_extras(_skills, {"skill": "無い", "skill_effects": [{"effect_id": 1}]}) == []
      and mod.skill_extras(_skills, {"skill": "強者への追従", "skill_effects": [{"effect_id": 9}]}) == []
      and mod.skill_extras(None, {"skill": "強者への追従"}) == [])

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

# 仲間の武器: 素点の基礎へ 2×√(能力 × 武器) × 率 を足す。上乗せにしかならない
check("ally gear: a weapon adds 2*sqrt(ability*weapon) at the rate",
      abs(mod.ally_gear_bonus(260, 323, percent=50) - 2 * (260 * 323) ** 0.5 * 0.5) < 1e-9)
check("ally gear: the rate comes from the setting by default",
      mod.ally_gear_bonus(260, 323) == mod.ally_gear_bonus(260, 323, percent=mod.ALLY_GEAR_PERCENT))
check("ally gear: rate 0 and broken inputs add nothing",
      mod.ally_gear_bonus(260, 323, percent=0) == 0 and mod.ally_gear_bonus(None, 323) == 0
      and mod.ally_gear_bonus("x", 323) == 0 and mod.ally_gear_bonus(260, None) == 0)
check("gear defense: the armor is added at the rate",
      mod.gear_defense(260, 268, percent=50) == 260 + 134 and mod.gear_defense(294, 100, percent=100) == 394
      and mod.gear_defense(294, None) == 294 and mod.gear_defense(294, 0) == 294
      and mod.gear_defense(294, 500, percent=0) == 294)

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

    def superseded(self):
        return False              # テスト中に注入し直しは起きない（`llm.wrap_outgoing` の見張りが聞く）

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

# 仲間の自己強化スキルが、状態異常と与ダメの倍率になる（期限はスキルの duration）。
_ctx = _fresh()
_mei = types.SimpleNamespace(name="メイ", max_hp=1108, current_hp=1108, experience_level=68,
                             status={}, skills=_skills)
_app = types.SimpleNamespace(current_enemy_dict={}, player=_mei)
_ctx.hooks["__main__:BattlePhaseManager.convert_llm_output_to_instruction_dict"](
    lambda *a, **k: {}, types.SimpleNamespace(app=_app), _mei, None,
    {"skill": "強者への追従", "skill_effects": [{"effect_id": 1, "targets": ["メイ"]}],
     "additional_effects": []})
check("skill buff: written as a status with the skill's duration",
      _mei.status.get("敏捷強化", {}).get("duration") == 2, repr(_mei.status))
check("skill buff: logged as a restored attribute effect",
      "restored attribute effect: '敏捷強化' on メイ" in _log(), _log())
check("skill buff: nothing was swallowed", not _ctx.errors, _ctx.errors)

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

# 戦闘の開始で敵の HP を3つ組で伸ばす。2回目（戦闘中のセーブから読み直した敵など）は触らない。
_ctx = _fresh()
_goblin = types.SimpleNamespace(name="ゴブリン", experience_level=20, max_hp=227, current_hp=227,
                                original_max_hp=227, status={},
                                ability_scores={"constitution": 11.37})
_hero = types.SimpleNamespace(name="エリス", max_hp=300, current_hp=300, original_max_hp=300,
                              experience_level=20, status={})
_app = types.SimpleNamespace(current_enemy_dict={"ゴブリン1": _goblin}, player=_hero,
                             add_text=lambda text: None)
_start = _ctx.hooks["__main__:BattleStartManager.start_battle"]
check("enemy hp: start_battle returns the game's result",
      _start(lambda self: "started", types.SimpleNamespace(app=_app)) == "started")
_stretched = (_goblin.original_max_hp, _goblin.max_hp, _goblin.current_hp)
check("enemy hp: all three HP fields are stretched together",
      _stretched == (568, 568, 568), repr(_stretched))
_start(lambda self: None, types.SimpleNamespace(app=_app))
check("enemy hp: a second start does not stretch again",
      (_goblin.original_max_hp, _goblin.max_hp, _goblin.current_hp) == _stretched)
check("enemy hp: the hero is untouched", (_hero.max_hp, _hero.current_hp) == (300, 300))
check("enemy hp: the stretch is logged", "enemy hp: ゴブリン 227 -> 568" in _log(), _log())

# 1発の置き換えを1手の流れで通す（calculate の素点 → get_instant_damage）。
_phase = types.SimpleNamespace(app=_app)
_calc = _ctx.hooks["__main__:BattlePhaseManager.calculate_battle_effect"]
_damage = _ctx.hooks["scripts.functions:get_instant_damage"]
_defense = _ctx.hooks["scripts.characters:Character.get_npc_defense"]
_got = {}


def _enemy_turn(*args, **kwargs):
    action = {"instant_damage": [{"target": "エリス", "power": "weak", "multiplier": 1,
                                  "category": "physical"}]}
    _calc(lambda self, a: [{"エリス": 114}] + [{}] * 7, _phase, action)
    _got["enemy"] = _damage(lambda a, d: a - d, 114, 89)


def _hero_turn(*args, **kwargs):
    action = {"instant_damage": [{"target": "ゴブリン1", "power": "weak", "multiplier": 1.5,
                                  "category": "physical"}]}
    _calc(lambda self, a: [{"ゴブリン1": 169}] + [{}] * 7, _phase, action)
    defense = _defense(lambda self: 57, _goblin)
    _got["hero"] = _damage(lambda a, d: a - d, 169, defense)


_saved_find_app = _ui.find_app
_ui.find_app = lambda: _app
try:
    _turn = _ctx.hooks["__main__:BattlePhaseManager.handle_battle_situation"]
    _turn(_enemy_turn, _phase, "ゴブリン1", "敵側", None)
    _turn(_hero_turn, _phase, "エリス", "味方側", None)
finally:
    _ui.find_app = _saved_find_app
check("turn: an enemy blow uses the game's raw and the armor, scaled for enemies",
      _got.get("enemy") == mod.hit_damage([("weak", 1)], 114, 89, 300, enemy=True) == 8,
      repr(_got))
check("turn: the hero's blow applies the referee's multiplier to the ratio",
      _got.get("hero") == mod.hit_damage([("weak", 1.5)], 169, 57, 568) == 190, repr(_got))
check("turn: both blows are logged", _log().count("hit: ") == 2, _log())
check("turn: nothing was swallowed", not _ctx.errors, _ctx.errors)

# 同名の敵が並ぶ戦闘（`Character.name` に連番は付かない。GAME.md §2.10）。
# 片方に付いた効果は、もう片方の手番で帳簿ごと消えず、もう片方の与ダメにも掛からない。
_ctx = _fresh()
_guard1 = types.SimpleNamespace(name="衛兵", experience_level=20, max_hp=568, current_hp=568,
                                status={})
_guard2 = types.SimpleNamespace(name="衛兵", experience_level=20, max_hp=568, current_hp=568,
                                status={})
_hero = types.SimpleNamespace(name="エリス", experience_level=20, max_hp=300, current_hp=300,
                              status={})
_app = types.SimpleNamespace(current_enemy_dict={"衛兵1": _guard1, "衛兵2": _guard2},
                             player=_hero, add_text=lambda text: None)
_phase = types.SimpleNamespace(app=_app)
_ctx.hooks["__main__:BattlePhaseManager.convert_llm_output_to_instruction_dict"](
    lambda *a, **k: {}, _phase, None, None,
    {"additional_effects": [
        {"type": "text_status", "target": ["衛兵1"], "status_name": "泥の浸食",
         "description": "...", "duration": 3, "intensity": 3,
         "effects_per_turn": [{"type": "instant_damage", "target": ["衛兵1"],
                               "power": "weak"}]},
        {"type": "reduction", "target": ["衛兵1"], "attribute_type": "str",
         "power": "strong"}]})
# text_status はゲームが `resolve_battle_effect` の中で status へ書く
_guard1.status["泥の浸食"] = {"status_name": "泥の浸食", "description": "...", "duration": 3}
_tick = _ctx.hooks["__main__:BattlePhaseManager.reduce_status_turns_and_log"]
_tick(lambda *a, **k: None, _phase, _guard2)
_tick(lambda *a, **k: None, _phase, _guard1)
check("same-named enemies: the other one's turn does not drop the per-turn effect",
      _guard1.current_hp == 568 - mod.per_turn_amount(568, "weak", 3)
      and _guard2.current_hp == 568, (_guard1.current_hp, _guard2.current_hp))
_got = {}


def _guard_blow(who):
    def turn(*args, **kwargs):
        action = {"instant_damage": [{"target": "エリス", "power": "weak", "multiplier": 1,
                                      "category": "physical"}]}
        _ctx.hooks["__main__:BattlePhaseManager.calculate_battle_effect"](
            lambda self, a: [{"エリス": 114}] + [{}] * 7, _phase, action)
        _got[who] = _ctx.hooks["scripts.functions:get_instant_damage"](lambda a, d: a - d, 114, 89)
    return turn


_turn = _ctx.hooks["__main__:BattlePhaseManager.handle_battle_situation"]
_turn(_guard_blow("衛兵2"), _phase, "衛兵2", "敵側", None)
_turn(_guard_blow("衛兵1"), _phase, "衛兵1", "敵側", None)
check("same-named enemies: a strength drop weakens only the one it was put on",
      _got.get("衛兵2") == mod.hit_damage([("weak", 1)], 114, 89, 300, enemy=True)
      and _got.get("衛兵1") == mod.hit_damage([("weak", 1)], 114, 89, 300, out_mult=0.8,
                                              enemy=True)
      and _got["衛兵1"] != _got["衛兵2"], repr(_got))
check("same-named enemies: nothing was swallowed", not _ctx.errors, _ctx.errors)

# ---------------------------------------------------------------- 回避と見切り
check("evasion: 2.5% per point of Dexterity over the attacker",
      abs(mod.evasion_chance(30, 12, 20, 20) - 0.45) < 1e-9)
check("evasion: the stat part stops at its cap, weaker foes add on top, the total stops at 90%",
      mod.evasion_chance(40, 10, 20, 20) == 0.5
      and abs(mod.evasion_chance(40, 10, 30, 20) - 0.8) < 1e-9
      and mod.evasion_chance(40, 10, 40, 20) == 0.9)
check("evasion: a stronger attacker takes the same rate away (uphill)",
      abs(mod.evasion_chance(30, 12, 20, 30) - 0.15) < 1e-9
      and mod.evasion_chance(13, 12, 20, 30) == 0.0)
_saved = mod.EVASION_UPHILL
mod.EVASION_UPHILL = False
check("evasion: uphill OFF leaves the stat part alone",
      abs(mod.evasion_chance(30, 12, 20, 30) - 0.45) < 1e-9)
mod.EVASION_UPHILL = _saved
check("evasion: unreadable stats count as no edge",
      mod.evasion_chance(None, None, None, None) == 0.0)
_roll = (0.3, 0.1, 0.45, 0.25)
check("evasion outcome: dodge first, then graze, else the blow lands",
      mod.evasion_outcome(_roll) == "evade"
      and mod.evasion_outcome((0.5, 0.1, 0.45, 0.25)) == "graze"
      and mod.evasion_outcome((0.5, 0.3, 0.45, 0.25)) is None)
check("evasion hint: one sentence for the referee",
      mod.evasion_hint("エリス", _roll) == "- エリス: この手番に受ける攻撃は身のこなしで完全にかわす（傷を負わない）"
      and mod.evasion_hint("エリス", (0.5, 0.1, 0.45, 0.25))
      == "- エリス: この手番に受ける攻撃は当たるが、急所を外して浅い傷で済む（かわしはしない。傷は負う）",
      mod.evasion_hint("エリス", _roll))
check("evasion hint: nothing to say when nothing happens",
      mod.evasion_hint("エリス", (0.9, 0.9, 0.45, 0.25)) is None)

# 審判を呼ぶ前に振り、頼み文へ足し、同じ判定でその手のダメージを 0 にする。
_ctx = _fresh()
_goblin = types.SimpleNamespace(name="ゴブリン", experience_level=20, max_hp=568, current_hp=568,
                                status={}, ability_scores={"dexterity": 12, "wisdom": 12})
_hero = types.SimpleNamespace(name="エリス", experience_level=20, max_hp=300, current_hp=300,
                              status={}, ability_scores={"dexterity": 30, "wisdom": 13})
_said = []
_app = types.SimpleNamespace(current_enemy_dict={"ゴブリン1": _goblin}, player=_hero,
                             add_text=_said.append)
_phase = types.SimpleNamespace(app=_app)
_chat = _ctx.hooks[_ml.llm.CHAT_TARGET]
_sent = {}


class _Rolls(object):
    def __init__(self, values):
        self.values = list(values)

    def random(self):
        return self.values.pop(0)


def _enemy_referee(player, combat_log, actor_name, actor, side, party, enemies):
    _chat(lambda self, model, messages, *a, **k: _sent.setdefault("messages", messages),
          None, "model", [{"role": "system", "content": "今、プレイヤーの**敵側**である'ゴブリン'の行動ターンです。"},
                     {"role": "user", "content": "本文"}])
    return {"skill": "通常攻撃"}


def _enemy_turn(*args, **kwargs):
    action = {"instant_damage": [{"target": "エリス", "power": "normal", "multiplier": 1,
                                  "category": "physical"}]}
    _ctx.hooks["__main__:BattlePhaseManager.calculate_battle_effect"](
        lambda self, a: [{"エリス": 137}] + [{}] * 7, _phase, action)
    _sent["damage"] = _ctx.hooks["scripts.functions:get_instant_damage"](lambda a, d: a - d, 137, 89)


_saved_rng, _saved_find_app = mod._RNG, _ui.find_app
mod._RNG = _Rolls([0.1, 0.9])            # エリスの回避 0.1 < 45%
_ui.find_app = lambda: _app
try:
    _ctx.hooks["scripts.llm.llm_manager_battle:referee_npc"](
        _enemy_referee, None, "log", "ゴブリン", _goblin, "敵側", {}, {})
    _outside = _chat(lambda self, model, messages, *a, **k: messages, None, "model",
                     [{"role": "user", "content": "別の頼み"}])
    _ctx.hooks["__main__:BattlePhaseManager.handle_battle_situation"](
        _enemy_turn, _phase, "ゴブリン1", "敵側", None)
finally:
    mod._RNG, _ui.find_app = _saved_rng, _saved_find_app
_last = _sent.get("messages", [{}])[-1].get("content", "")
check("evasion: the referee is told before it writes the turn",
      mod.EVASION_HEADER in _last and "- エリス:" in _last and _last.startswith("本文"), _last)
check("evasion: other requests are left alone", _outside[-1]["content"] == "別の頼み", _outside)
check("evasion: the same roll makes the blow miss", _sent.get("damage") == 0, _sent)
check("evasion: the line comes after the turn", _said == ["（エリスは攻撃をかわした）"], _said)
check("evasion: logged", "EVADED" in _log() and "evasion: told the referee" in _log(), _log())
check("evasion: nothing was swallowed", not _ctx.errors, _ctx.errors)

# 回避した手では画面を揺らさない。1手の中でも、閉じた直後でも。当たった手は揺らす。
_SHAKE = "scripts.hud.new_hud:InstanTaleHUD.shake_window"


def _shake_turn(rolls):
    """敵の1手を流し、手の中と閉じた直後に揺れを1回ずつ呼ぶ。揺れた回数を返す。"""
    global _ctx
    _ctx = _fresh()
    shaken = []

    def turn(*args, **kwargs):
        _enemy_turn()
        _ctx.hooks[_SHAKE](lambda self, root: shaken.append("in"), None, "root")

    mod._RNG = _Rolls(rolls)
    _ui.find_app = lambda: _app
    try:
        _ctx.hooks["scripts.llm.llm_manager_battle:referee_npc"](
            _enemy_referee, None, "log", "ゴブリン", _goblin, "敵側", {}, {})
        _ctx.hooks["__main__:BattlePhaseManager.handle_battle_situation"](
            turn, _phase, "ゴブリン1", "敵側", None)
        _ctx.hooks[_SHAKE](lambda self, root: shaken.append("after"), None, "root")
    finally:
        mod._RNG, _ui.find_app = _saved_rng, _saved_find_app
    return shaken


_said[:] = []
check("shake: an evaded blow does not shake the screen, in or just after the action",
      _shake_turn([0.1, 0.9]) == [] and "shake: skipped, all evaded (in the action)" in _log()
      and "shake: skipped, all evaded (just after the action)" in _log(), _log())
_said[:] = []
check("shake: a landed blow still shakes", _shake_turn([0.9, 0.9]) == ["in", "after"], _log())
_said[:] = []
check("shake: a grazed blow still shakes", _shake_turn([0.9, 0.0]) == ["in", "after"], _log())
check("shake: nothing was swallowed", not _ctx.errors, _ctx.errors)

# 既定では敵は避けない（主人公の手では振らない）。
_ctx = _fresh()
mod._RNG = _Rolls([])
_ui.find_app = lambda: _app
try:
    _ctx.hooks["scripts.llm.llm_manager_battle:referee_player_attack_new_new"](
        lambda *a, **k: "attacked", "log", _hero, {}, {})
finally:
    mod._RNG, _ui.find_app = _saved_rng, _saved_find_app
check("evasion: enemies do not dodge by default", "evasion rolled" not in _log(), _log())

# 防御ボタンの連打。2発目は捨て、次のスキル一覧が出たらまた押せる（実機で 0.18 秒差の2発が1巡を2回走らせた）。
_ctx = _fresh()
_chosen = []
_guard_app = types.SimpleNamespace(
    player=_hero, current_enemy_dict={}, add_text=lambda text: None,
    buttons=[{"text": "防御", mod.MARK: "guard"}], display_button_map=None,
    function_correspond_to_input=None,
    process_choice=lambda manager, text: _chosen.append(text))
_press = _ctx.hooks["__main__:InstantaleApp.on_button_press"]
_press(lambda *a, **k: None, _guard_app, 0)
_press(lambda *a, **k: None, _guard_app, 0)
check("guard: a second press while the turn runs is ignored",
      "guard button ignored" in _log() and _log().count("guard button pressed") == 1, _log())
_ctx.hooks["__main__:SkillChoicePhaseManager.display_skill_choices"](lambda self: None, object())
_press(lambda *a, **k: None, _guard_app, 0)
check("guard: the next skill list lets it be pressed again", _log().count("guard button pressed") == 2, _log())

# 防御ボタンの手番は、審判が出したダメージを入れない（敵の攻撃を描いて主人公の手の結果にすることがある。実機）。
_ctx = _fresh()
_got = {}


def _guard_turn(*args, **kwargs):
    action = {"instant_damage": [{"target": "エリス", "power": "normal", "multiplier": 1,
                                  "category": "physical"}]}
    _ctx.hooks["__main__:BattlePhaseManager.calculate_battle_effect"](
        lambda self, a: [{"エリス": 644}] + [{}] * 7, _phase, action)
    _got["self"] = _ctx.hooks["scripts.functions:get_instant_damage"](lambda a, d: a - d, 644, 201)


_ui.find_app = lambda: _app
try:
    _ctx.hooks["scripts.llm.llm_manager_battle:referee_player_any_input_new_new"](
        lambda *a, **k: {"narration": "..."}, "log", _hero, mod.GUARD_COMMAND, {}, {})
    _ctx.hooks["__main__:BattlePhaseManager.handle_battle_situation"](
        _guard_turn, _phase, "エリス", "味方陣営", None)
finally:
    _ui.find_app = _saved_find_app
check("guard turn: the hero's own guard deals no damage", _got.get("self") == 0, _got)
check("guard turn: logged", "GUARD-TURN" in _log(), _log())
_ctx2 = _fresh()
_ui.find_app = lambda: _app
try:
    _ctx2.hooks["scripts.llm.llm_manager_battle:referee_player_any_input_new_new"](
        lambda *a, **k: {}, "log", _hero, "盾を構えつつ斬りかかる", {}, {})
finally:
    _ui.find_app = _saved_find_app
check("guard turn: a free input with guard words is not a guard turn", "GUARD-TURN" not in _log())

# ---------------------------------------------------------------- 通常攻撃の頼み
# 通常攻撃の審判の頼み文にだけ一文を足す。仲間・敵の審判の似た行には当てない（実記録の文面）。
_attack = ("【生成要素】\n- modifications: これまでの戦闘の流れから、今回の効果に対してもっともらしい修正がある場合にそれを記入する。\n"
           + mod.BASIC_ATTACK_LINE + "\n- vfx: 画面に表示するvfxを選択する。")
_npc = ("- additional_effects: 戦闘の状況や流れからスキル詳細とは別の追加効果が発生すると考えられる場合、"
        "その内容を記入する。追加が無い場合は空のリストを返す。")
_asked = mod.steady_basic_attack(_attack)
check("basic attack: the rule follows the additional_effects line",
      mod.BASIC_ATTACK_LINE + mod.BASIC_ATTACK_RULE + "\n- vfx" in _asked, _asked)
check("basic attack: asking twice adds it once",
      mod.steady_basic_attack(_asked) == _asked)
check("basic attack: other referees and other text are untouched",
      mod.steady_basic_attack(_npc) == _npc and mod.steady_basic_attack("x") == "x"
      and mod.steady_basic_attack(None) is None)
_ctx = _fresh()
check("basic attack: the outgoing text passes through the loader's hook",
      _ml.llm.CHAT_TARGET in _ctx.hooks, sorted(_ctx.hooks))

print()
if failures:
    print("FAILED: {}".format(", ".join(failures)))
    raise SystemExit(1)
print("all ok")
