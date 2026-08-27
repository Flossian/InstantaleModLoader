# -*- coding: utf-8 -*-
"""319_battle_tactics の数の芯をゲーム抜きで通す。

    python tools/tests/test_battle_tactics.py

モジュール直下の純関数だけを叩く（フックの側は実機で確かめる。
VERIFICATION.md の 3xx の表）。

  帯       … power の列挙 → 割合。知らない語は normal 扱い
  強さ比   … 格差のカーブと [0.4, 2.5] の留め
  軽減     … 防御の飽和曲線と上限 60%
  1発      … 実測の帯（§2.68）で 格下2〜3手 / 同格5〜8手 / ボス10手前後 に落ちる
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

# ---------------------------------------------------------------- 強さ比
check("strength: equals are 1.0", mod.strength_ratio(1000, 1000) == 1.0)
check("strength: capped above", mod.strength_ratio(100000, 1) == mod.STRENGTH_MAX)
check("strength: capped below", mod.strength_ratio(1, 100000) == mod.STRENGTH_MIN)
check("strength: bad input is 1.0", mod.strength_ratio(None, 0) == 1.0)

# ---------------------------------------------------------------- 軽減
check("mitigation: zero defense", mod.mitigation(0) == 0.0)
check("mitigation: capped", mod.mitigation(10 ** 9) == mod.MITIGATION_MAX)
check("mitigation: pivot halves",
      abs(mod.mitigation(mod.MITIGATION_PIVOT) - 0.5) < 1e-9
      or mod.mitigation(mod.MITIGATION_PIVOT) == mod.MITIGATION_MAX)
check("mitigation: bad input", mod.mitigation("armor") == 0.0)

# ---------------------------------------------------------------- 1発（実測の帯）
# プレイヤー max_hp 1560、格下の敵 460 / 防御 96、ボス（2倍格）3120 / 防御 500。
# VERIFICATION_LOG.md §2.68 の面々。
mob = mod.hit_fraction([("weak", 1.5)], 1560, 460, 96)
check("hit: a mob falls in 2-4 actions", 0.25 <= mob <= 0.50, "fraction={}".format(mob))

equal = mod.hit_fraction([("normal", 1)], 1560, 1560, 200)
check("hit: equals need 5-9 actions", 0.11 <= equal <= 0.20,
      "fraction={}".format(equal))

boss = mod.hit_fraction([("normal", 1)], 1560, 3120, 300)
check("hit: a boss needs about 10", 0.07 <= boss <= 0.14, "fraction={}".format(boss))

nuke = mod.hit_fraction([("extreme", 1)], 3120, 1560, 500)
check("hit: a boss nuke hurts but does not one-shot", 0.30 <= nuke <= 0.65,
      "fraction={}".format(nuke))

guarded = mod.hit_fraction([("extreme", 1)], 3120, 1560, 500, in_mult=0.5)
check("hit: guarding halves the nuke", abs(guarded - nuke / 2) < 1e-9
      or guarded == mod.MIN_FRACTION, "fraction={}".format(guarded))

check("hit: never below the floor",
      mod.hit_fraction([("weak", 0.01)], 1, 10 ** 6, 10 ** 6) == mod.MIN_FRACTION)
check("hit: equals stay under the cap even at the worst",
      mod.hit_fraction([("extreme", 1.5)], 1560, 1560, 0)
      == mod.MAX_FRACTION / 100.0)

# ---------------------------------------------------------------- 一撃の解禁
check("one-shot cap: closed for equals",
      mod.oneshot_cap(1.0) == mod.MAX_FRACTION / 100.0
      and mod.oneshot_cap(mod.ONESHOT_START) == mod.MAX_FRACTION / 100.0)
check("one-shot cap: fully open past the threshold",
      mod.oneshot_cap(mod.ONESHOT_FULL) == 1.0 and mod.oneshot_cap(12.0) == 1.0)
mid = mod.oneshot_cap((mod.ONESHOT_START + mod.ONESHOT_FULL) / 2)
check("one-shot cap: opens gradually in between",
      mod.MAX_FRACTION / 100.0 < mid < 1.0, "cap={}".format(mid))
check("one-shot: a legendary hero fells a starter slime in one blow",
      mod.hit_fraction([("normal", 1)], 1560, 64, 0, base_value=883) == 1.0)
check("one-shot: an overwhelming boss fells a novice in one blow",
      mod.hit_fraction([("extreme", 1)], 5000, 300, 50) == 1.0)
check("hit: bad multiplier falls to 1",
      mod.hit_fraction([("weak", "x")], 1000, 1000, 0)
      == mod.hit_fraction([("weak", 1)], 1000, 1000, 0))

# ---------------------------------------------------------------- 武器（基礎値）
# 実測: エリスの基礎値 883 = 2×√(390×500)。武器 23 なら 189（§2.68 / §2.10.2）。
check("gear: the measured loadout vs an equal is about 1.0",
      0.9 <= mod.gear_factor(883, 1560) <= 1.15,
      "factor={}".format(mod.gear_factor(883, 1560)))
check("gear: capped both ways",
      mod.gear_factor(10 ** 9, 100) == mod.STRENGTH_MAX
      and mod.gear_factor(1, 10 ** 9) == mod.STRENGTH_MIN)
check("gear: bad input is 1.0",
      mod.gear_factor(None, 1560) == 1.0 and mod.gear_factor(883, 0) == 1.0)

# 武器 23 → 500（基礎値 189 → 883。§2.68）の全幅。どちらも留めに掛からない
# 相手（格下460）で測る。指数 0.5 の初版は 2.2倍で、強化の実感が薄かった。
with_sword = mod.hit_fraction([("normal", 1)], 1560, 460, 96, base_value=883)
with_knife = mod.hit_fraction([("normal", 1)], 1560, 460, 96, base_value=189)
check("gear: the full weapon range roughly triples the damage",
      2.9 <= with_sword / with_knife <= 3.4,
      "ratio={}".format(with_sword / with_knife))

# 「武器の効きの強さ」を上げると同じ武器差がさらに開く。
saved = mod.WEAPON_IMPACT
mod.WEAPON_IMPACT = 100
steeper = (mod.hit_fraction([("normal", 1)], 1560, 460, 96, base_value=883)
           / mod.hit_fraction([("normal", 1)], 1560, 460, 96, base_value=189))
mod.WEAPON_IMPACT = saved
check("gear: raising WEAPON_IMPACT widens the gap",
      steeper > with_sword / with_knife, "steeper={}".format(steeper))
check("gear: even the best weapon cannot one-shot an equal",
      mod.hit_fraction([("extreme", 1.5)], 1560, 1560, 0, base_value=2000)
      == mod.MAX_FRACTION / 100.0)
check("gear: no base value falls back to the max_hp ratio",
      mod.hit_fraction([("normal", 1)], 1560, 1560, 200)
      == mod.hit_fraction([("normal", 1)], 1560, 1560, 200, base_value=None))

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
check("level: the cap opens by level gap alone",
      mod.oneshot_cap(1.0, gap=20) == 1.0
      and mod.oneshot_cap(1.0, gap=15) == mod.MAX_FRACTION / 100.0
      and mod.MAX_FRACTION / 100.0 < mod.oneshot_cap(1.0, gap=17) < 1.0)
check("level: the lower side never gets the open cap",
      mod.oneshot_cap(1.0, gap=-40) == mod.MAX_FRACTION / 100.0)
same = mod.hit_fraction([("normal", 1)], 1560, 1560, 200,
                        attacker_level=60, defender_level=60)
elite = mod.hit_fraction([("normal", 1)], 1560, 1560, 200,
                         attacker_level=60, defender_level=45)
check("level: an elite-gap attacker hits twice as hard",
      abs(elite - same * 2) < 1e-9, "same={} elite={}".format(same, elite))
outclassed = mod.hit_fraction([("extreme", 1)], 3120, 1560, 500,
                              attacker_level=80, defender_level=60)
check("level: an outclassing boss can one-shot",
      outclassed == 1.0, "fraction={}".format(outclassed))
chip = mod.hit_fraction([("normal", 1)], 1560, 1560, 200,
                        attacker_level=40, defender_level=60)
check("level: hitting upward is divided by the same rate",
      abs(chip - same / 3) < 1e-9 or chip == mod.MIN_FRACTION,
      "chip={}".format(chip))

# ---------------------------------------------------------------- 仲間の底上げ
weak_ally = mod.hit_fraction([("weak", 1)], 132, 460, 96,
                             attacker_level=36, defender_level=36,
                             ally_floor=True)
weak_raw = mod.hit_fraction([("weak", 1)], 132, 460, 96,
                            attacker_level=36, defender_level=36)
check("ally floor: a low-HP member fights at par",
      weak_ally > weak_raw * 2
      and abs(weak_ally - mod.band_of("weak") * (1 - mod.mitigation(96))) < 1e-9,
      "floored={} raw={}".format(weak_ally, weak_raw))
check("ally floor: does not shrink a member already above par",
      mod.hit_fraction([("weak", 1)], 900, 460, 96, ally_floor=True)
      == mod.hit_fraction([("weak", 1)], 900, 460, 96))

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
