# -*- coding: utf-8 -*-
"""319_battle_tactics の数の芯をゲーム抜きで通す。

    python tools/tests/test_battle_tactics.py

モジュール直下の純関数だけを叩く（フックの側は実機で確かめる。
VERIFICATION.md の 3xx の表）。

  帯       … power の列挙 → 火力に乗せる割合。知らない語は normal 扱い
  軽減     … 防御の飽和曲線と上限 60%
  上限     … 互角は 65%。レベル差か火力差で開き、開き切ると外れる
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

# ---------------------------------------------------------------- 一撃の上限
check("cap: closed for equals",
      mod.cap_fraction(0, 1.0) == mod.MAX_FRACTION / 100.0
      and mod.cap_fraction(15, 0) == mod.MAX_FRACTION / 100.0
      and mod.cap_fraction(0, mod.DOMINANCE_START) == mod.MAX_FRACTION / 100.0)
check("cap: fully open past either threshold",
      mod.cap_fraction(mod.LEVEL_OUTCLASS_GAP, 0) is None
      and mod.cap_fraction(0, mod.DOMINANCE_FULL) is None)
check("cap: opens gradually in between",
      mod.MAX_FRACTION / 100.0 < mod.cap_fraction(17, 0) < 1.0
      and mod.MAX_FRACTION / 100.0 < mod.cap_fraction(0, 2.25) < 1.0)
check("cap: the lower side never gets the open cap",
      mod.cap_fraction(-40, 0.1) == mod.MAX_FRACTION / 100.0)

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
nuke = mod.hit_damage([("extreme", 1)], 1500, 1560, 500,
                      attacker_level=75, defender_level=62)
check("hit: an elite boss nuke is capped short of a one-shot",
      nuke == int(round(1560 * mod.MAX_FRACTION / 100.0)),
      "final={}".format(nuke))
guarded = mod.hit_damage([("extreme", 1)], 1500, 1560, 500, in_mult=0.5,
                         attacker_level=75, defender_level=62)
check("hit: guarding halves what gets through even at the cap",
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
check("wobble: a high roll still cannot break the cap",
      mod.hit_damage([("extreme", 1)], 1500, 1560, 500, wobble=1.5,
                     attacker_level=62, defender_level=62)
      == int(round(1560 * mod.MAX_FRACTION / 100.0)))

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

# ---------------------------------------------------------------- 倍率
name, description, book = mod.attribute_recipe("reduction", "str", "strong")
check("attribute: str reduction lowers outgoing damage",
      name == "筋力低下" and abs(book["out_mult"] - 0.80) < 1e-9, repr(book))
name, description, book = mod.attribute_recipe("enhancement", "con", "weak")
check("attribute: con enhancement lowers incoming damage",
      name == "耐久強化" and abs(book["in_mult"] - 0.90) < 1e-9, repr(book))
check("attribute: unknown attributes are skipped",
      mod.attribute_recipe("enhancement", "luck", "weak") is None)

print()
if failures:
    print("FAILED: {}".format(", ".join(failures)))
    raise SystemExit(1)
print("all ok")
