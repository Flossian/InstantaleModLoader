# -*- coding: utf-8 -*-
"""926_body_augmentation の規則をゲーム抜きで通す。

    python tools/tests/test_wip_body_augmentation.py

  見立て … LLM の答えの検算（表に無い部位・能力値・特殊効果を落とす、断りの理由、名前の長さ）。伸びと代償は重ねない
  量     … レア度・仕立て・違法で量の段が動く。特殊効果は設定のレア度から、違法は必ず。違法には暴走か侵食が付く
  代償   … 能力値の代償の幅。戦闘の代償は合法なら能力値と片方（見立てごとに固定）、違法は両方。量と表示
  料金   … 料金・日数・取り外しの割合。違法の割増
  失敗   … 仕立てとレア度で上がり、耐久と判断で下がる。下限と上限
  枠     … 合法は1つめだけ、違法は2つめに重ねられる
  合計   … 能力値と戦闘の効果の足し方。同調・痛覚遮断
  日数   … 侵食と自己修復の刻み、上限
  世界観 … 判定の検算と、設定の語の優先
  設定   … mod.json の既定値と入口の定数が一致する
"""
import ast
import importlib.util
import json
import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
MODS_DIR = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir, "runtime", "mods"))


def find_mod(suffix):
    matches = sorted(name for name in os.listdir(MODS_DIR)
                     if name.endswith(suffix) and os.path.isfile(os.path.join(MODS_DIR, name, "mod.json")))
    if len(matches) != 1:
        raise SystemExit("cannot find the mod *{}: {}".format(suffix, matches))
    return os.path.join(MODS_DIR, matches[0])


MOD_DIR = find_mod("_body_augmentation")
PACKAGE = "instantale_mod_test_body_augmentation"


def load(name):
    if PACKAGE not in sys.modules:
        package = types.ModuleType(PACKAGE)
        package.__path__ = [MOD_DIR]
        sys.modules[PACKAGE] = package
    full = "{}.{}".format(PACKAGE, name)
    spec = importlib.util.spec_from_file_location(full, os.path.join(MOD_DIR, name + ".py"))
    module = importlib.util.module_from_spec(spec)
    sys.modules[full] = module
    spec.loader.exec_module(module)
    return module


rules = load("rules")
prompts = load("prompts")

FAILURES = []


def check(cond, what):
    if not cond:
        FAILURES.append(what)
        print("FAIL", what)


INFO = {"name": "人工筋繊維", "description": "伸縮する繊維の束", "item_type": "material", "rarity": "rare",
        "value": 12, "detail": "other_material"}


def test_appraisal():
    raw = {"usable": "true", "part": "arms", "name": "強化義腕ほげほげほげほげほげ", "description": "x" * 100,
           "boost": "strength, 器用, nonsense", "cost": "charisma", "special": "teleport", "finish": "weird"}
    got = rules.normalize_appraisal(raw)
    check(got and got["part"] == "arms", "part kept")
    check(len(got["name"]) == rules.NAME_LIMIT, "name cut")
    check(len(got["description"]) == rules.DESCRIPTION_LIMIT, "description cut")
    check(got["boost"] == ["strength", "dexterity"], "boost from keys and names: {}".format(got["boost"]))
    check(got["cost"] == "charisma", "cost from the answer: {}".format(got["cost"]))
    check(got["special"] == "pierce", "unknown special -> the part's first")
    check(got["finish"] == rules.PROCESSED, "unknown finish -> processed")
    check(rules.normalize_appraisal({"usable": "true", "part": "tail", "name": "x"}) is None, "bad part -> None")
    check(rules.normalize_appraisal({"usable": "true", "part": "腕", "name": "x"})["part"] == "arms", "japanese part")
    refused = rules.normalize_appraisal({"usable": "false", "reason": "紙は体に入らない"})
    check(refused == {"usable": False, "reason": "紙は体に入らない", "rule": rules.APPRAISAL_RULE}, "refused")
    check(got["rule"] == rules.APPRAISAL_RULE, "rule version on usable")
    check(rules.normalize_appraisal("text") is None, "not a dict")
    got = rules.normalize_appraisal({"usable": "true", "part": "head", "name": "x", "special": "painblock"})
    check(got["special"] == "painblock" and got["boost"] == ["intelligence"] and got["cost"] == "wisdom",
          "head defaults {}".format(got))
    check(got["drawback"] == "falter", "head drawback default {}".format(got))
    got = rules.normalize_appraisal({"usable": "true", "part": "body", "name": "x", "drawback": "FRAGILE"})
    check(got["drawback"] == "fragile", "drawback from the answer")
    got = rules.normalize_appraisal({"usable": "true", "part": "body", "name": "x", "drawback": "反動"})
    check(got["drawback"] == "backlash", "drawback from the japanese name")
    got = rules.normalize_appraisal({"usable": "true", "part": "organs", "name": "x", "drawback": "weak"})
    check(got["drawback"] == "unmend", "unknown drawback -> part default")
    got = rules.normalize_appraisal({"usable": "true", "part": "arms", "name": "x", "boost": "charisma",
                                     "cost": "charisma"})
    check(got["boost"] == ["charisma"] and got["cost"] == "dexterity", "cost equal to boost -> part default")
    check(rules.normalize_appraisal({"usable": "true", "part": "legs", "name": "x", "special": "painblock"})
          ["special"] == "evade", "special of another part is dropped")


def test_costs():
    check(rules.cost_for("arms", ["strength"], "wisdom") == "wisdom", "any ability can be the cost")
    check(rules.cost_for("arms", ["dexterity"], "dexterity") == "charisma" or
          rules.cost_for("arms", ["dexterity"], "dexterity") not in ("dexterity",), "never the boosted one")
    check(rules.cost_for("arms", ["strength"], None) == "dexterity", "no answer -> part default")
    check(rules.cost_for("arms", ["dexterity"], None) == "strength", "default boosted -> first free ability")
    check(rules.cost_for("arms", ["strength"], "nonsense") == "dexterity", "unknown -> part default")
    for part in rules.PARTS:
        check(rules.PART_BOOST[part] != rules.PART_COST[part], "default boost differs from cost: " + part)
    check(rules.cost_range("common", False) == (0, 1) and rules.cost_range("magical", False) == (0, 1),
          "legal low 0..1")
    check(rules.cost_range("epic", False) == (1, 2) and rules.cost_range("mythic", False) == (1, 2), "legal high 1..2")
    check(rules.cost_range("rare", True) == (1, 3) and rules.cost_range("legendary", True) == (2, 4),
          "illegal 1..3 / 2..4")
    for part in rules.PARTS:
        check(rules.PART_COST[part] != "charisma", "default cost is not charisma: " + part)
        check(rules.PART_DRAWBACK[part] in rules.DRAWBACKS, "default drawback: " + part)

    class Fixed(object):
        def __init__(self, pick):
            self.pick = pick

        def randint(self, low, high):
            return low if self.pick == "low" else high

    record = rules.make_record(appraisal(), INFO, None, False, 0, 1, "", rng=Fixed("low"))
    check("strength" not in record["abilities"], "cost 0 leaves no cost")
    record = rules.make_record(appraisal(), INFO, None, False, 0, 1, "", rng=Fixed("high"))
    check(record["abilities"]["strength"] == -1, "cost 1")
    record = rules.make_record(appraisal(), dict(INFO, rarity="mythic"), None, True, 0, 1, "", rng=Fixed("high"))
    check(record["abilities"]["strength"] == -4, "illegal up to 4")
    check(rules.cost_piece(appraisal(), "rare", False) == ("筋力 0〜−1", rules.MINUS), "estimate 0..-1")
    check(rules.cost_piece(appraisal(), "epic", True) == ("筋力 −2〜−4", rules.MINUS), "estimate -2..-4")


def test_drawbacks():
    ability = appraisal()                     # 腕・義腕は能力値の側
    battle = dict(appraisal(), name="義手")   # 腕・義手は戦闘の側
    check(rules.cost_side(ability) == rules.COST_ABILITY and rules.cost_side(battle) == rules.COST_BATTLE,
          "sides of the test appraisals")
    check(rules.cost_side(dict(ability)) == rules.cost_side(ability), "the side is fixed per appraisal")
    legal = rules.build_effects(ability, "epic", False)
    check(legal["drawback"] == {} and legal["abilities"]["strength"] == -2, "legal ability side {}".format(legal))
    legal = rules.build_effects(dict(battle, drawback="fragile"), "epic", False)
    check(legal["drawback"] == {"fragile": 5} and "strength" not in legal["abilities"],
          "legal battle side {}".format(legal))
    check(rules.cost_piece(battle, "epic", False) is None, "no ability estimate on the battle side")
    check(rules.build_effects(dict(battle, drawback="unmend"), "common", False)["drawback"] == {"unmend": 15},
          "legal unmend 15")
    low = rules.build_effects(dict(ability, drawback="falter"), "magical", True)
    check(low["drawback"] == {"falter": 10} and low["abilities"]["strength"] == -3, "illegal low both {}".format(low))
    high = rules.build_effects(dict(battle, drawback="unmend"), "epic", True)
    check(high["drawback"] == {"unmend": 45} and high["abilities"]["strength"] == -4, "illegal high both {}".format(high))
    check(rules.build_effects(ability, "rare", True)["drawback"] == {"backlash": 10}, "missing drawback -> part default")
    record = rules.make_record(dict(battle, drawback="fragile"), INFO, None, True, 0, 1, "")
    check(record["drawback"] == {"fragile": 10}, "record keeps the drawback")
    check(rules.battle_totals([record, record]) == {"pierce": 40, "critical": 50, "fragile": 20},
          "drawbacks add up {}".format(rules.battle_totals([record, record])))
    costs = [text for text, _kind in rules.effect_pieces(record, costs=True)]
    check("脆さ 受けるダメージ +10%" in costs, "drawback in the costs {}".format(costs))
    check(("脆さ 10%", rules.MINUS) in rules.effect_pieces(record, compact=True), "compact drawback")
    check(all(text != "脆さ 受けるダメージ +10%" for text, _k in rules.effect_pieces(record, costs=False)),
          "drawback not among the effects")
    _stats, lines = rules.total_pieces([record])
    check(("脆さ 10%", rules.MINUS) in lines, "drawback in the totals {}".format(lines))
    for kind in rules.DRAWBACKS:
        check(kind in rules.DRAWBACK_HINTS and kind in rules.DRAWBACK_NAMES, "named drawback " + kind)


def appraisal(part="arms", finish=rules.PROCESSED, special="pierce", boost=("dexterity",), cost="strength"):
    boost = list(boost)
    return {"usable": True, "part": part, "name": "義腕", "description": "", "boost": boost,
            "cost": rules.cost_for(part, boost, cost), "special": special, "finish": finish}


def test_amounts():
    legal = [rules.build_effects(appraisal(), r, False)["abilities"]["dexterity"] for r in rules.RARITIES]
    check(legal == [1, 1, 2, 2, 3, 4], "legal amounts {}".format(legal))
    illegal = [rules.build_effects(appraisal(), r, True)["abilities"]["dexterity"] for r in rules.RARITIES]
    check(illegal == [2, 2, 3, 4, 5, 6], "illegal amounts {}".format(illegal))
    raw = rules.build_effects(appraisal(finish=rules.RAW), "epic", False)["abilities"]["dexterity"]
    check(raw == 2, "raw drops one tier (epic 3 -> 2): {}".format(raw))
    check(rules.build_effects(appraisal(finish=rules.RAW), "epic", True)["abilities"]["dexterity"] == 4,
          "illegal raw does not drop")
    check(rules.build_effects(appraisal(), "rare", False)["abilities"]["strength"] == -1, "estimate: legal low -1")
    check(rules.build_effects(appraisal(), "epic", False)["abilities"]["strength"] == -2, "estimate: legal high -2")
    check(rules.build_effects(appraisal(), "mythic", True)["abilities"]["strength"] == -4, "estimate: illegal high -4")
    check("strength" not in rules.build_effects(appraisal(), "rare", False, loss=0)["abilities"], "loss 0")
    two = appraisal(boost=("dexterity", "constitution"))
    effects = rules.build_effects(two, "legendary", False)["abilities"]
    check(effects["dexterity"] == 3 and effects["constitution"] == 1, "second boost halves: {}".format(effects))
    check("constitution" not in rules.build_effects(two, "common", False)["abilities"], "half of 1 drops")


def test_specials():
    low = rules.build_effects(appraisal(), "magical", False)
    check(low["battle"] == {} and low["special"] == {}, "no special below epic")
    check(rules.build_effects(appraisal(), "epic", False)["battle"] == {"pierce": 20}, "epic special")
    check(rules.build_effects(appraisal(), "legendary", False)["battle"] == {"pierce": 35}, "legendary special")
    check(rules.build_effects(appraisal(), "common", False, special_min=0)["battle"] == {"pierce": 20},
          "setting lowers the rarity")
    check(rules.build_effects(appraisal(), "mythic", False, special_min=5)["battle"] == {"pierce": 50},
          "setting raises the rarity")
    head = rules.build_effects(appraisal("head", special="painblock", boost=("intelligence",)), "legendary", False)
    check(head["special"] == {"painblock": 75} and head["battle"] == {}, "own special {}".format(head))
    arms = rules.build_effects(appraisal(), "common", True)
    check(arms["battle"] == {"pierce": 20, "critical": 25} and arms["special"] == {"rampage": 1},
          "illegal arms: special + rampage {}".format(arms))
    organs = rules.build_effects(appraisal("organs", special="selfrepair", boost=("constitution",), cost="wisdom"),
                                 "epic", True)
    check(organs["special"] == {"selfrepair": 4, "erosion": 4} and organs["abilities"]["constitution"] == 4 + 1,
          "illegal organs: special + erosion {}".format(organs))
    for part, pool in rules.PART_SPECIALS.items():
        for kind in pool:
            check(kind in rules.BATTLE_STRENGTH or kind in rules.OWN_STRENGTH, "known special " + kind)
            check(kind in rules.SPECIAL_HINTS and kind in rules.SPECIAL_NAMES, "named special " + kind)


def test_fees():
    config = rules.Config()
    check(rules.install_fee(INFO, False, config) == 200 * 2 + 20 * 12, "legal fee")
    check(rules.install_fee(INFO, True, config) == int(round((200 * 2 + 20 * 12) * 1.5)), "illegal fee")
    check(rules.install_days(INFO, False, config) == 10, "legal days")
    check(rules.install_days(INFO, True, config) == 3, "illegal days")
    record = rules.make_record(appraisal(), INFO, {"name": "x"}, True, 0, 1, "裏")
    check(rules.remove_fee(record, config) == (200 * 2 + 20 * 12) // 2, "remove at medical uses legal fee")
    check(rules.remove_fee(record, config, at_underworld=True) == int(round((200 * 2 + 20 * 12) * 1.5 / 2)),
          "remove at underworld uses illegal fee")
    check(rules.remove_days(record, config) == 5, "remove days half")
    custom = rules.Config(fee_base=1000, days_percent=200, fee_per_value=True, special_min=0)
    check(custom.fee_per_value == 20 and custom.special_min == 0, "bool is not a number")
    check(rules.install_days(INFO, False, custom) == 20, "days percent")


def test_fail():
    config = rules.Config()
    check(rules.fail_percent(INFO, rules.PROCESSED, False, False, {}, config) == 8.0, "processed rare 8%")
    check(rules.fail_percent(dict(INFO, rarity="mythic"), rules.RAW, False, False, {}, config) == 21.0, "raw mythic")
    strong = rules.fail_percent(INFO, rules.PROCESSED, False, False, {"constitution": 31, "wisdom": 31}, config)
    check(strong == 1.0, "floor 1%: {}".format(strong))
    illegal = rules.fail_percent(INFO, rules.PROCESSED, True, True, {"constitution": 11, "wisdom": 11}, config)
    check(illegal == 45.0, "illegal overload 45%: {}".format(illegal))
    floor = rules.fail_percent(INFO, rules.PROCESSED, True, False, {"constitution": 99, "wisdom": 99}, config)
    check(floor == 10.0, "illegal floor 10%: {}".format(floor))
    check(rules.fail_percent(INFO, rules.PROCESSED, True, True, {}, rules.Config(illegal_fail=99)) == 90.0, "cap 90%")


def test_slots_and_totals():
    bucket = rules.new_bucket()
    check(rules.free_slot(bucket, "arms", False) == 0, "empty -> slot 0")
    bucket["augments"]["arms"][0] = rules.make_record(appraisal(), INFO, None, False, 0, 1, "")
    check(rules.free_slot(bucket, "arms", False) is None, "legal cannot stack")
    check(rules.free_slot(bucket, "arms", True) == 1, "illegal stacks")
    bucket["augments"]["arms"][1] = rules.make_record(appraisal(), dict(INFO, rarity="epic"), None, True, 1, 1, "")
    check(rules.free_slot(bucket, "arms", True) is None, "full")
    check(rules.has_illegal(bucket), "has illegal")
    records = [r for _p, _s, r in rules.records_of(bucket)]
    totals = rules.ability_totals(records)
    check(totals == {"strength": -1 - 4, "dexterity": 1 + 4}, "ability totals {}".format(totals))
    battle = rules.battle_totals(records)
    check(battle == {"pierce": 35, "critical": 35, "backlash": 15}, "battle totals {}".format(battle))
    stats, lines = rules.total_pieces(records)
    check(("器用", "+5", rules.PLUS) in stats, "total pieces {}".format(stats))
    check(lines[-1] == ("暴走（腕）", rules.MINUS), "wear last {}".format(lines))
    fixed, changed = rules.normalize_bucket({"augments": {"arms": [None]}, "history": None})
    check(changed and fixed["augments"]["arms"] == [None, None] and fixed["history"] == [], "normalize")
    ordered = rules.order_bucket(fixed)
    check(list(ordered["augments"]) == list(rules.PARTS) and ordered["wear_days"] == 0, "order")


def test_sync_and_painblock():
    def one(part, special, rarity="epic"):
        return rules.make_record(appraisal(part, special=special, boost=(rules.PART_BOOST[part],)),
                                 dict(INFO, rarity=rarity), None, False, 0, 1, "")
    records = [one("head", "sync"), one("arms", "pierce"), one("body", "reduce")]
    check(rules.battle_totals(records)["reduce"] == 5 + 1 * 3, "sync adds 1% per augmented part")
    full = [one(part, "sync" if part == "head" else rules.PART_SPECIALS[part][0]) for part in rules.PARTS]
    plain = [dict(record, special={}) for record in full]
    for key in rules.ABILITIES:
        check(rules.ability_totals(full).get(key, 0) - rules.ability_totals(plain).get(key, 0) == 1,
              "full set +1 " + key)
        check(rules.ability_totals(records).get(key, 0)
              == rules.ability_totals([dict(r, special={}) for r in records]).get(key, 0), "no bonus at 3 parts " + key)
    pain = [one("head", "painblock", "legendary")]
    base = rules.ability_totals(pain)
    got = rules.ability_totals(pain, {"strength": 4, "dexterity": 0})
    check(got.get("strength", 0) - base.get("strength", 0) == 3 and got.get("dexterity") == base.get("dexterity"),
          "painblock 75% of 4 -> 3: {} / {}".format(got, base))
    both = pain + [one("sanity", "painblock", "mythic")]
    check(rules.ability_totals(both, {"strength": 4}).get("strength", 0)
          - rules.ability_totals(both).get("strength", 0) == 4, "painblock capped at 100%")


def test_wear():
    check(rules.wear_steps(10, 25) == (1, 5) and rules.wear_steps(0, 90) == (3, 0), "wear steps")
    erosion = rules.make_record(appraisal("organs", special="regen", boost=("constitution",)), INFO, None, True, 0, 1, "")
    repair = rules.make_record(appraisal("body", special="selfrepair", boost=("strength",)),
                               dict(INFO, rarity="epic"), None, False, 0, 1, "")
    start = {"head": 20, "organs": 0, "arms": 100, "legs": 0}
    check(rules.apply_wear(start, [erosion], 2) == {"organs": 6}, "erosion +3 per step")
    check(rules.apply_wear({"organs": 98}, [erosion], 3) == {"organs": 99}, "erosion stops at 99")
    check(rules.apply_wear({"organs": 120}, [erosion], 3) == {}, "erosion leaves a lost part alone")
    check(rules.apply_wear(start, [repair], 3) == {"head": 14}, "selfrepair -2 per step, not the lost arm")
    check(rules.apply_wear({"organs": 10}, [erosion, repair], 1) == {"organs": 11}, "selfrepair -2 against erosion +3")
    check(rules.apply_wear(start, [], 5) == {}, "nothing without records")
    rampage = rules.make_record(appraisal(), INFO, None, True, 0, 1, "")
    check(rules.rampage_parts([rampage, erosion]) == ["arms"], "rampage parts")


def test_theme():
    check(rules.normalize_theme({"theme": "magic", "title": "魔導施術とても長い題です", "illegal": "禁術禁術禁術禁"})
          == {"theme": "magic", "title": "魔導施術とても長い題", "illegal": "禁術禁術禁術"}, "theme cut")
    check(rules.normalize_theme({"theme": "machine", "title": "躯体改造", "illegal": "非正規改造"})["illegal"]
          == "非正規改造", "a word a little over the asked length is kept")
    check(rules.normalize_theme({"theme": "機械"})["theme"] == "machine", "japanese theme")
    check(rules.normalize_theme({"theme": "space"}) is None, "unknown theme")
    detected = {"theme": "magic", "title": "刻印術", "illegal": "外法"}
    words = rules.words_for("magic", detected)
    check(words["title"] == "刻印術" and words["illegal"] == "外法", "detected words")
    check(rules.words_for("machine", detected)["title"] == "人体改造", "detected words only for the same theme")
    words = rules.words_for("magic", detected, title="手術", illegal="")
    check(words["title"] == "手術" and words["illegal"] == "外法", "setting wins")


def test_labels():
    check(rules.underworld_label({"illegal": "違法", "augment": "改造"}) == "違法の改造", "plain label")
    check(rules.underworld_label({"illegal": "禁術改造", "augment": "改造"}) == "禁術改造", "label already has the word")
    record = {"part": "arms", "abilities": {"strength": 2, "dexterity": -1, "charisma": 1},
              "battle": {"pierce": 20}, "special": {"rampage": 1, "sync": 2}}
    kinds = [kind for _text, kind in rules.effect_pieces(record)]
    check(kinds == [rules.PLUS] * 4 + [rules.MINUS] * 2, "plus first: {}".format(kinds))
    costs = [text for text, _kind in rules.effect_pieces(record, costs=True)]
    check(costs == ["器用 −1", "暴走 敵を倒すたびに腕の怪我 +1"], "costs {}".format(costs))


def test_batch():
    raw = {"items": [
        {"index": "1", "usable": "true", "part": "arms", "name": "義腕"},
        {"index": "2", "usable": "false", "reason": "装身具"},
        {"index": "2", "usable": "true", "part": "head", "name": "重複"},    # 重複は先を採る
        {"index": "9", "usable": "true", "part": "head", "name": "範囲外"},
        {"index": "x", "usable": "true", "part": "head", "name": "番号なし"},
        {"index": "3", "usable": "true", "part": "tail", "name": "部位が無い"},
        "text"]}
    got = rules.normalize_batch(raw, 3)
    check(sorted(got) == [0, 1], "batch keys {}".format(sorted(got)))
    check(got[0]["part"] == "arms" and got[1]["usable"] is False, "batch values")
    check(rules.normalize_batch(None, 3) == {} and rules.normalize_batch({"items": "x"}, 3) == {}, "batch junk")
    check([len(c) for c in rules.chunks(list(range(23)))] == [10, 10, 3], "chunks of ten")
    check(rules.current_appraisal({"usable": True}) is None, "old appraisal is redone")
    check(rules.current_appraisal(got[0]) is got[0], "current appraisal kept")
    check(rules.current_appraisal(None) is None, "no appraisal")
    for kind in ("weapon", "wearable", "healing_item", "consumable"):
        check(rules.item_info({"name": "x", "item_type": kind}) is not None, "{} is appraised".format(kind))


def test_free_spot():
    check(rules.free_spot([], (1, 1)) == (0, 0), "empty grid")
    check(rules.item_size({"width_slots": 2, "height_slots": 2}) == (2, 2), "size from a dict")
    check(rules.item_size({}) == (1, 1) and rules.item_size(object()) == (1, 1), "size falls back to 1x1")
    full = [((x, y), (1, 1)) for x in range(4) for y in range(6)]
    check(rules.free_spot(full, (1, 1)) is None, "full grid")
    one_gap = [p for p in full if p[0] != (3, 5)]
    check(rules.free_spot(one_gap, (1, 1)) == (3, 5), "the only gap")
    check(rules.free_spot(one_gap, (1, 2)) is None, "a tall item does not fit one cell")
    # 縦長の品（1x4）は y から上へ4マス。欄の上に出る位置は置き直す品として詰める
    tall = [((0, 0), (1, 4)), ((1, 4), (1, 4))]
    check(rules.free_spot(tall, (1, 1)) == (2, 0), "overflowing item is repacked first: {}".format(
        rules.free_spot(tall, (1, 1))))
    overlap = [((0, 0), (1, 1)), ((0, 0), (1, 1))]
    check(rules.free_spot(overlap, (1, 1)) == (2, 0), "overlapping item is repacked first")
    check(rules.free_spot([((None, 1), (1, 1))] + one_gap, (1, 1)) is None, "repacking fills the last gap")
    check(rules.free_spot([(["x"], (1, 1))], (1, 1)) == (1, 0), "unreadable position is repacked")


def test_candidate_notes():
    tally = {"pending": 2, "refused": 5, "failed": 1, "unknown": 0}
    note = rules.candidate_note(tally, True)
    check(note == "鑑定中 2個・適合しない 5個・鑑定に失敗 1個（開き直すと頼み直す）", note)
    check(rules.candidate_note({"unknown": 3}, False) == "未鑑定 3個（施設で開くと鑑定する）", "view only")
    check(rules.candidate_note({"unknown": 3}, False, hint=False) == "未鑑定 3個", "view only, hint shown above")
    check(rules.candidate_note({}, True) == "", "nothing hidden")
    check(rules.candidate_empty({"pending": 1}, True) == "鑑定している……", "empty while pending")
    check("クラフト" in rules.candidate_empty({"refused": 2}, True), "craft hint")
    check("クラフト" in rules.candidate_empty({}, True), "craft hint with an empty bag")
    check("治療院" in rules.candidate_empty({"unknown": 2}, False), "view only, not appraised yet")


def test_prompts():
    other = dict(INFO, name="銀細工の髪飾り")
    message = prompts.appraise_message([INFO, other], "剣と魔法の世界", False)
    text = message[0]["content"]
    check(isinstance(message, list) and "1. 名前: 人工筋繊維" in text and "2. 名前: 銀細工の髪飾り" in text,
          "numbered items")
    check("arms（腕）" in text and "【体に適合する品】" in text and "index:" in text, "fit rule and index")
    check("cost:" in text and "painblock=" in text and "一点もの" in text, "cost asked, own specials listed")
    check(set(prompts.APPRAISE_FIELDS) == {"index", "usable", "part", "name", "description", "boost", "cost",
                                           "drawback", "special", "finish", "reason"}, "fields")
    check("drawback:" in text and "fragile=" in text and "charisma にしない" in text, "drawback asked, charisma bias")
    check("闇医者" in prompts.appraise_message([INFO], "", True)[0]["content"], "illegal doctor")
    check("machine" in prompts.theme_message("", "")[0]["content"], "theme message")


def test_settings_match():
    manifest = json.load(open(os.path.join(MOD_DIR, "mod.json"), encoding="utf-8"))
    tree = ast.parse(open(os.path.join(MOD_DIR, "body_augmentation.py"), encoding="utf-8").read())
    values = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            try:
                values[node.targets[0].id] = ast.literal_eval(node.value)
            except Exception:
                pass
    for name, spec in manifest["settings"].items():
        check(values.get(name) == spec["default"], "setting {} default".format(name))
    world = values.get("WORLD_SETTINGS") or ()
    check(set(world) == set(manifest["settings"]), "every setting can be set per world")
    check(manifest["settings"]["SPECIAL_MIN_RARITY"]["values"] == list(rules.RARITIES), "rarity choices")


def main():
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    if FAILURES:
        print("{} failure(s)".format(len(FAILURES)))
        return 1
    print("ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
