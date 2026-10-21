# -*- coding: utf-8 -*-
"""339_town_factions をゲーム抜きで通す。

    python tools/tests/test_town_factions.py

確認するもの:

  規則     … 点数と段の対応／根拠の照合（空白と括弧を無視・短すぎる根拠は認めない）
             ／台帳を組む（根拠の無い勢力・街・関係は捨てる・勢力が2つ未満なら作らない・平和な世界の下限）
             ／初回の判定を点数に起こす（呼び名は判定した段のときだけ）／会話の判定（1日1回・敵対する勢力へ響く）
             ／情勢（1段まで・設定の関係は2回押されて動く・逆向きで数え直す・平和な世界の下限）
  材料     … 街だけを拾う・素の案内文と周辺の土地の文を入れる・338 の出来事を「前回から」で読む
  流れ     … 世界ごとに有効にするまで何もしない／読み込みで台帳を作る（抽出 → 初回の判定）
             ／勢力が1つの世界では作らず、読み込みのたびに作り直さない
             ／会話の構え（相手の名簿の街の勢力・仲間には足さない・中立には足さない）と情勢の噂
             ／要約に相乗りして見られ方を動かす（337 と同じ要約に2本乗っても送信は1回）
             ／日数が進むと、338 の出来事を片付けた街の勢力が上がり、間隔が来たら情勢を進める
"""
import importlib.util
import io
import json
import os
import re
import shutil
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
RUNTIME_DIR = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir, "runtime"))
MODS_DIR = os.path.join(RUNTIME_DIR, "mods")
OUT_DIR = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir, "out", "test"))
STATE_DIR = os.path.join(OUT_DIR, "state_town_factions")

if RUNTIME_DIR not in sys.path:
    sys.path.insert(0, RUNTIME_DIR)

import instantale_modloader as ml                      # noqa: E402
from instantale_modloader import hudicons, llm, modnpc, talk_summary, ui  # noqa: E402


def find_mod(suffix):
    matches = sorted(name for name in os.listdir(MODS_DIR)
                     if name.endswith(suffix)
                     and os.path.isfile(os.path.join(MODS_DIR, name, "mod.json")))
    if len(matches) != 1:
        raise SystemExit("cannot find exactly one *{} in {}: {}".format(suffix, MODS_DIR, matches))
    folder = os.path.join(MODS_DIR, matches[0])
    with io.open(os.path.join(folder, "mod.json"), encoding="utf-8") as fh:
        entry = json.load(fh)["entry"]
    return folder, os.path.join(folder, entry)


MOD_DIR, MOD = find_mod("_town_factions")
MOD_NAME = "town_factions_mod"
failures = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name
          + ((" -- " + str(detail)) if detail and not cond else ""))
    if not cond:
        failures.append(name)


def load_package():
    for name in list(sys.modules):
        if name == MOD_NAME or name.startswith(MOD_NAME + "."):
            sys.modules.pop(name, None)
    spec = importlib.util.spec_from_file_location(
        MOD_NAME, MOD, submodule_search_locations=[MOD_DIR])
    module = importlib.util.module_from_spec(spec)
    sys.modules[MOD_NAME] = module
    spec.loader.exec_module(module)
    return module


module = load_package()
rules = sys.modules[MOD_NAME + ".rules"]
material = sys.modules[MOD_NAME + ".material"]
view = sys.modules[MOD_NAME + ".view"]

# ================================================================ 規則
print("[規則] 点数と段")
check("段の境目", [rules.standing_stage(s) for s in (-100, -61, -60, -21, -20, 19, 20, 59, 60, 100)]
      == [-2, -2, -1, -1, 0, 0, 1, 1, 2, 2])
check("数を均す", [rules.to_int(v, -2, 2) for v in (5, "-1", "+2", "1.0", None, True, float("nan"))]
      == [2, -1, 2, 1, None, None, None])

print("[規則] 根拠の照合")
MATERIAL = "白の教団は遺物を罪の証として排除する。\n灰の結社は 古代文明を崇拝し、白の教団と敵対する。"
check("原文にあれば通る", rules.evidence_ok("遺物を罪の証として排除", MATERIAL))
check("空白と括弧は無視する", rules.evidence_ok("「灰の結社は古代文明を崇拝し」", MATERIAL))
check("原文に無ければ落とす", not rules.evidence_ok("白の教団は世界を支配している", MATERIAL))
check("短すぎる根拠は認めない", not rules.evidence_ok("灰の結", MATERIAL)
      and rules.evidence_ok("灰の結社", MATERIAL))
check("途中を省いた根拠は切れ目ごとに照合する",
      rules.evidence_ok("白の教団は...古代文明を崇拝し", MATERIAL)
      and rules.evidence_ok("灰の結社は… 白の教団と敵対する。", MATERIAL)
      and not rules.evidence_ok("白の教団は...世界を支配している", MATERIAL))
check("短い語をつないだ根拠は認めない", not rules.evidence_ok("白の...敵対", MATERIAL)
      and not rules.evidence_ok("...", MATERIAL))

print("[規則] 台帳を組む")
EXTRACTED = {
    "factions": [
        {"name": "白の教団", "kind": "宗教組織", "note": "遺物を罪とみなす", "evidence": "遺物を罪の証として排除"},
        {"name": "灰の結社", "kind": "結社", "note": "古代を崇める", "evidence": "古代文明を崇拝し"},
        {"name": "魔王軍", "kind": "軍", "note": "", "evidence": "魔王軍が北に攻めてくる"}],
    "towns": [{"town_id": "1", "faction": "灰の結社", "evidence": "灰の結社は古代文明を崇拝し"},
              {"town_id": "0", "faction": "灰の結社", "evidence": "古代文明を崇拝し"},
              {"town_id": "7", "faction": "白の教団", "evidence": "白の教団は遺物を罪"},
              {"town_id": "9", "faction": "白の教団", "evidence": "白の教団は遺物を罪"},
              {"town_id": "4", "faction": "工房", "evidence": "白の教団は遺物を罪"},
              {"town_id": "8", "faction": "白の教団", "evidence": "どこにも無い文章です"}],
    "relations": [{"a": "白の教団", "b": "灰の結社", "stage": -2, "evidence": "白の教団と敵対する"}],
    "peaceful": "false"}
EXTRACTED["factions"].append({"name": "東の街の支配勢力", "kind": "組み立てた名前", "note": "",
                              "evidence": "遺物を罪の証として排除"})
ledger, dropped = rules.build_ledger(EXTRACTED, MATERIAL, ["0", "1", "4", "7", "8"], day=100)
check("設定に無い呼び名の勢力は捨てる", any("東の街の支配勢力" in d and "name" in d for d in dropped), dropped)
EXTRACTED["factions"].pop()
STAND_IN = MATERIAL + "\n冒険者の代わりに、端末で依頼を請ける契約屋がいる。契約屋の組合は白の教団と取引する。"
row = {"name": "契約屋", "kind": "", "note": "", "evidence": "端末で依頼を請ける契約屋がいる"}
_l, dropped2 = rules.build_ledger(dict(EXTRACTED, factions=EXTRACTED["factions"] + [row]), STAND_IN,
                                  ["1"], day=1)
check("冒険者に当たる人々は勢力にしない", any("契約屋" in d and "adventurers" in d for d in dropped2)
      and [f["name"] for f in _l["factions"]] == ["白の教団", "灰の結社"], dropped2)
check("冒険者と同じ文でなければ落とさない", not rules.is_adventurer_stand_in("白の教団", STAND_IN))
GUILD = "谷の中心地で、ギルド『氷の輪』の支部が置かれ、冒険者と交易隊の宿場を兼ねる。"
check("名前の付いた冒険者ギルドは勢力にしない", rules.is_adventurer_stand_in("氷の輪", GUILD))
check("冒険者の無い文のギルドは落とさない",
      not rules.is_adventurer_stand_in("金秤", "商人ギルド『金秤』が港を仕切る。"))
GENERAL = MATERIAL + "\n裏通りでは金貸しが幅をきかせる。"
row = {"name": "金貸し", "name_type": "一般", "kind": "", "note": "", "evidence": "裏通りでは金貸しが幅をきかせる"}
_l, dropped3 = rules.build_ledger(dict(EXTRACTED, factions=EXTRACTED["factions"] + [row]), GENERAL,
                                  ["1"], day=1)
check("一般の呼び名は勢力にしない", any("金貸し" in d and "general" in d for d in dropped3)
      and [f["name"] for f in _l["factions"]] == ["白の教団", "灰の結社"], dropped3)
check("根拠の無い勢力は捨てる", [f["name"] for f in ledger["factions"]] == ["白の教団", "灰の結社"]
      and any("魔王軍" in d for d in dropped), (ledger["factions"], dropped))
check("街の勢力（知らない街・知らない勢力・根拠の無い街は捨てる）",
      ledger["towns"] == {"1": {"faction": "f2", "evidence": "灰の結社は古代文明を崇拝し"},
                          "7": {"faction": "f1", "evidence": "白の教団は遺物を罪"}}, ledger["towns"])
check("勢力の名前の無い根拠の街は捨てる", any(d.startswith("town 0:") and "does not name" in d
                                            for d in dropped), dropped)
NAMED = MATERIAL + "\nカスティアとヴァルガルドを治める「灰の結社」は、秩序を重んじる。\n東の港町ミストルは霧に包まれている。"
by_town = dict(EXTRACTED, towns=[
    {"town_id": "2", "faction": "灰の結社", "evidence": "東の港町ミストルは霧に包まれている"},
    {"town_id": "3", "faction": "灰の結社", "evidence": "東の港町ミストルは霧に包まれている"},
    {"town_id": "5", "faction": "白の教団", "evidence": "東の港町ミストルは霧に包まれている"}])
named, dropped4 = rules.build_ledger(by_town, NAMED, ["2", "3", "5"], day=1,
                                     town_names={"2": "カスティア", "3": "ヴァルガルド", "5": "ミストル"})
check("根拠に勢力の名前が無くても、設定に勢力と街を1文で結ぶ文があればそれを根拠にする",
      named["towns"].get("2") == {"faction": "f2",
                                  "evidence": "カスティアとヴァルガルドを治める「灰の結社」は、秩序を重んじる"}
      and named["towns"].get("3", {}).get("faction") == "f2", (named["towns"], dropped4))
check("勢力と街を結ぶ文が無ければ、今までどおり捨てる",
      "5" not in named["towns"] and any(d.startswith("town 5:") and "does not name" in d
                                        for d in dropped4), dropped4)
check("街の名前が渡されなければ救わない（今までの形）",
      rules.build_ledger(by_town, NAMED, ["2", "3", "5"], day=1)[0]["towns"] == {})
check("1字の名前では文を探さない", rules.sentence_naming(NAMED, "灰の結社", "カ") is None)
reworded = dict(EXTRACTED, towns=[
    {"town_id": "2", "faction": "灰の結社", "evidence": "カスティアは結社の支配下にある"},
    {"town_id": "5", "faction": "灰の結社", "evidence": "ミストルは結社の支配下にある"}])
named2, dropped5 = rules.build_ledger(reworded, NAMED, ["2", "5"], day=1,
                                      town_names={"2": "カスティア", "5": "ミストル"})
check("言い換えた根拠でも、勢力と街を1文で結ぶ文があれば救う",
      named2["towns"].get("2", {}).get("faction") == "f2" and "5" not in named2["towns"]
      and any(d == "town 5: evidence not found" for d in dropped5), (named2["towns"], dropped5))
check("設定の関係", ledger["relations"]["f1|f2"]["stage"] == -2
      and ledger["relations"]["f1|f2"]["from_setting"], ledger["relations"])
check("見られ方は中立から", all(r["score"] == 0 for r in ledger["standing"].values()))
check("作った日", ledger["made_day"] == 100 and ledger["status"] == rules.READY)
peaceful = dict(EXTRACTED, peaceful="true")
check("平和な世界は緊張より下げない",
      rules.build_ledger(peaceful, MATERIAL, ["1"], day=1)[0]["relations"]["f1|f2"]["stage"] == -1)
one = {"factions": EXTRACTED["factions"][:1], "towns": [], "relations": []}
lone, _ = rules.build_ledger(one, MATERIAL, ["1"], day=1)
check("勢力が1つなら作らない", lone["status"] == rules.NONE and not lone["factions"], lone)

print("[規則] 初回の判定")
rules.apply_initial_standing(ledger, {"standings": [
    {"faction": "白の教団", "stage": -2, "title": "異端の拾い屋", "reason": "白の教団の街で追われている"},
    {"faction": "灰の結社", "stage": 0, "title": "使い走り", "reason": "特になし"},
    {"faction": "知らない勢力", "stage": 2, "title": "x", "reason": "x"}]})
check("段の真ん中の点数", ledger["standing"]["f1"]["score"] == -80
      and ledger["standing"]["f2"]["score"] == 0, ledger["standing"])
check("中立には呼び名を持たせない", ledger["standing"]["f2"]["title"] == "")
check("段階の名前は呼び名にしない", rules.title_of({"title": "盟友"}) == ""
      and rules.title_of({"title": " 異端の拾い屋 "}) == "異端の拾い屋")
stance = rules.stance_text(ledger, "f1", "検査の主人公")
check("構えの文と呼び名", "白の教団に属する者" in stance and "敵とみなして" in stance
      and "「異端の拾い屋」" in stance, stance)
check("中立には構えを足さない", rules.stance_text(ledger, "f2", "検査の主人公") == "")
ledger["standing"]["f1"]["score"] = -40
check("段が変われば呼び名は外す", "異端の拾い屋" not in rules.stance_text(ledger, "f1", "検査の主人公")
      and "信用していない" in rules.stance_text(ledger, "f1", "検査の主人公"))

print("[規則] 会話の判定")
ledger["standing"]["f1"]["score"] = 0
moves = rules.plan_talk(ledger, "f2", 2, gain_per_step=5, loss_per_step=8, gained_today=False)
check("上がる（敵対する勢力から半分引く）", moves == [("f2", 0, 10, "gain"), ("f1", 0, -5, "echo")], moves)
check("今日はもう上げた", rules.plan_talk(ledger, "f2", 1, gain_per_step=5, loss_per_step=8,
                                         gained_today=True) == [("f2", 0, 0, "daily")])
check("下がる方は響かない", rules.plan_talk(ledger, "f2", -2, gain_per_step=5, loss_per_step=8,
                                           gained_today=True) == [("f2", 0, -16, "loss")])
check("0 は動かない", rules.plan_talk(ledger, "f2", 0, gain_per_step=5, loss_per_step=8,
                                      gained_today=False) == [])
ledger["standing"]["f2"]["score"] = 98
check("上限で止まる", rules.plan_gain(ledger, "f2", 10)[0] == ("f2", 98, 100, "gain"))

print("[規則] 手配と敵対の上限")
cap_book = {"factions": [{"id": "f1"}, {"id": "f2"}],
            "towns": {"0": {"faction": "f1"}, "1": {"faction": "f2"}},
            "relations": {"f1|f2": {"stage": -2}},
            "standing": {"f1": {"score": -40}, "f2": {"score": 0}}}
check("上がる先の上限（手配なし・手配・手配度 -40 以下）",
      [rules.gain_ceiling(cap_book, "f1", laws) for laws in ({"0": 10}, {"0": -39}, {"0": -40}, {})]
      == [100, -21, -61, 100])
moves = rules.plan_talk(cap_book, "f1", 2, gain_per_step=15, loss_per_step=8, gained_today=False,
                        ceiling=rules.gain_ceiling(cap_book, "f1", {"0": -5}))
check("手配されていれば緊張の上端で止まり、響きは上がった分から", moves
      == [("f1", -40, -21, "gain"), ("f2", 0, -10, "echo")], moves)
cap_book["standing"]["f1"]["score"] = -21
check("上限にいれば動かない（響きも無い）",
      rules.plan_gain(cap_book, "f1", 10, ceiling=-21) == [("f1", -21, -21, "capped")])
cap_book["standing"]["f1"]["score"] = -76
rules.apply_moves(cap_book, rules.plan_talk(cap_book, "f1", -1, gain_per_step=5, loss_per_step=8,
                                            gained_today=False))
check("-81 以下で敵対が外れない印", cap_book["standing"]["f1"]["score"] == -84
      and cap_book["standing"]["f1"].get("locked") and not cap_book["standing"]["f2"].get("locked"))
rules.apply_moves(cap_book, rules.plan_gain(cap_book, "f1", 30,
                                            ceiling=rules.gain_ceiling(cap_book, "f1", {"0": 10})))
check("手配が無くなっても敵対の上端まで", cap_book["standing"]["f1"]["score"] == -61
      and rules.standing_stage(-61) == -2, cap_book["standing"])

print("[規則] 情勢")
plan = rules.plan_trend(ledger, "f1", "f2", +1)
check("設定の関係は1回目では動かない", plan["after"] == -2 and plan["push"] == 1, plan)
rules.apply_trend(ledger, "f1", "f2", plan, day=130)
plan = rules.plan_trend(ledger, "f1", "f2", +1)
check("2回目で1段動く", plan["after"] == -1 and plan["push"] == 0, plan)
rules.apply_trend(ledger, "f1", "f2", plan, day=160)
check("段と日を書く", ledger["relations"]["f1|f2"]["stage"] == -1
      and ledger["relations"]["f1|f2"]["updated_day"] == 160)
plan = rules.plan_trend(ledger, "f1", "f2", -1)
rules.apply_trend(ledger, "f1", "f2", plan, day=190)
plan2 = rules.plan_trend(ledger, "f1", "f2", +1)
check("逆向きに押されたら数え直す", plan["push"] == -1 and plan2["push"] == 1 and plan2["after"] == -1,
      (plan, plan2))
loose = {"peaceful": False, "relations": {"f1|f2": {"stage": 0, "push": 0, "from_setting": False}}}
check("設定に無い関係はすぐ動く", rules.plan_trend(loose, "f2", "f1", -1)["after"] == -1)
loose["peaceful"] = True
loose["relations"]["f1|f2"]["stage"] = -1
check("平和な世界は緊張より下げない", rules.plan_trend(loose, "f1", "f2", -1)["why"] == "at the limit")
check("知らない組", rules.plan_trend(loose, "f1", "f9", 1) is None)
check("間隔", rules.trend_due(100, 130, 30) and not rules.trend_due(100, 129, 30)
      and not rules.trend_due(None, 500, 30) and not rules.trend_due(100, 500, 0))

print("[規則] 手で直した台帳")
hand = rules.empty_ledger()
hand.update(status=rules.READY, made_day=5, factions=[
    {"id": "f1", "name": "魔王の軍勢"}, {"id": "f2", "name": "ルミナ王国"}, {"id": "f3", "name": "黄昏の商会"},
    {"id": "f3", "name": "二つ目の f3"}, {"id": "", "name": "id の無い勢力"}],
    standing={"f1": {"score": "-40"}, "f2": {"score": 150}},
    relations={"f2|f1": {"stage": -2, "push": 0, "from_setting": True}, "f1|f9": {"stage": 1}},
    towns={"1": {"faction": "f2"}, "2": {"faction": "f9"}},
    people={"30": {"town": "1", "faction": "f9", "role": "other"}, "31": {"town": "1", "faction": "f2", "role": "member"}})
fixed = rules.repair(hand)
check("同じ id・id の無い勢力を外す", [f["name"] for f in hand["factions"]] == ["魔王の軍勢", "ルミナ王国", "黄昏の商会"],
      hand["factions"])
check("見られ方の無い勢力は中立で足し、点数は範囲の整数に均す",
      hand["standing"]["f3"]["score"] == 0 and hand["standing"]["f1"]["score"] == -40
      and hand["standing"]["f2"]["score"] == 100, hand["standing"])
check("全部の組を揃える（並びを直し、無い組は中立、知らない勢力の組は外す）",
      sorted(hand["relations"]) == ["f1|f2", "f1|f3", "f2|f3"] and hand["relations"]["f1|f2"]["stage"] == -2
      and hand["relations"]["f1|f2"]["from_setting"] is True
      and hand["relations"]["f1|f3"] == {"stage": 0, "push": 0, "from_setting": False, "evidence": "",
                                         "updated_day": 5}, hand["relations"])
check("知らない勢力を指す街と住人を外す", hand["towns"] == {"1": {"faction": "f2"}}
      and list(hand["people"]) == ["31"], (hand["towns"], hand["people"]))
check("直したことを並べる", len(fixed) >= 8 and any("f1|f9" in line for line in fixed), fixed)
plan = rules.plan_trend(hand, "f3", "f1", -1)
rules.apply_trend(hand, "f3", "f1", plan, day=40)
check("足した組で情勢が動く", hand["relations"]["f1|f3"]["stage"] == -1, hand["relations"]["f1|f3"])
check("揃った台帳は直さない", rules.repair(hand) == [])
check("作る前の台帳には触らない", rules.repair(rules.empty_ledger()) == [])

print("[規則] 勢力の刺客")
check("確率は敵対なら敵対の値、緊張なら緊張の値、それより上は 0",
      rules.hunter_rate(-2, 20, 0) == 20 and rules.hunter_rate(-1, 20, 0) == 0
      and rules.hunter_rate(-1, 20, 15) == 15 and rules.hunter_rate(0, 20, 15) == 0
      and rules.hunter_rate(-2, 150, 0) == 100 and rules.hunter_rate(-2, "x", 0) == 0)
check("次の刺客までの日数", rules.hunter_due(None, 50, 10) and not rules.hunter_due(45, 50, 10)
      and rules.hunter_due(40, 50, 10) and not rules.hunter_due(40, None, 10))
pack = {"hunters": {"91": {"faction": "f1", "status": "escaped", "day": 30},
                    "90": {"faction": "f1", "status": "dead", "day": 10},
                    "92": {"faction": "f2", "status": "ready", "day": 5},
                    "93": {"faction": "f1", "status": "ready", "day": 40}}}
check("待っている刺客はその勢力の生きている古い順", rules.waiting_hunter(pack, "f1", lambda i: True) == "91"
      and rules.waiting_hunter(pack, "f1", lambda i: i != "91") == "93"
      and rules.waiting_hunter(pack, "f3", lambda i: True) is None)
check("討ったときの見られ方の動き", rules.hunter_loss({"standing": {"f1": {"score": -70}}}, "f1", 10)
      == [("f1", -70, -80, rules.HUNTER)] and rules.hunter_loss({"standing": {}}, "f1", 10) == [])
check("手配度はゲームが下げた分を重ねない", rules.wanted_after(10, 10, 10) == 0 and rules.wanted_after(10, 0, 10) == 0
      and rules.wanted_after(10, -5, 10) == -5 and rules.wanted_after(None, 10, 10) == 10
      and rules.wanted_after(10, 10, 0) == 10)
check("逃げた後の手配度は戦闘の前より下げない", rules.wanted_after_escape(0, -10) == 0
      and rules.wanted_after_escape(0, 5) == 5 and rules.wanted_after_escape(None, -10) == -10)

# ================================================================ 材料
print("[材料]")
SAVE = {
    "world_data": {"world_name": "勢力の検査世界", "days_elapsed": 100,
                   "overview": "白の教団は遺物を罪の証として排除する。灰の結社は古代文明を崇拝し、白の教団と敵対する。工房は中立。",
                   "structure_description": "中心から三方へ道が伸びる。",
                   "story": {"world_situation": "各地で遺物が暴走している。"}},
    "player_data": {"name": "検査の主人公"},
    "areas": {"0": {"name": "中央の町", "size": "town",
                    "descriptions": {"overview": "街道の要衝。", "area_description": "丘陵に囲まれた土地。"}},
              "1": {"name": "東の村", "size": "village",
                    "descriptions": {"overview": "灰の結社の監視が行き届いた村。"}},
              "7": {"name": "北の巡礼町", "size": "town",
                    "descriptions": {"overview": "白の教団が管理する巡礼の町。"}},
              "20": {"name": "地下墓地", "size": "dungeon", "descriptions": {"overview": "暗い"}}},
    # 店や施設の主は名簿に載らず、生まれた街（initial_location）で住まいが分かる
    "npcs": {"30": {"name": "中央の町の宿屋", "job": "inn", "profile": "街道の宿を営む。",
                    "initial_location": {"area": "0", "facility": "1"}},
             "31": {"name": "東の村の番人", "job": "guard", "profile": "灰の結社の監視役。",
                    "initial_location": {"area": "1", "facility": "2"}},
             "37": {"name": "北の巡礼町の司祭", "job": "church", "profile": "白の教団の司祭。",
                    "initial_location": {"area": "7", "facility": "3"}},
             "38": {"name": "酒場の主", "job": "inn", "profile": "教団の説教を嫌う。", "personality": "皮肉屋。",
                    "initial_location": {"area": "7", "facility": "4"}},
             "39": {"name": "古物商", "job": "general_store", "profile": "金にしか興味が無い。",
                    "initial_location": {"area": "7", "facility": "5"}},
             "40": {"name": "結社の使い", "job": "adventure", "profile": "灰の結社に通じている。",
                    "initial_location": {"area": "7", "facility": "6"}}}}
towns = material.towns_of(SAVE)
check("街だけを拾う", [t[0] for t in towns] == ["0", "1", "7"], towns)
text = material.extract_material(SAVE)
check("素の案内文と周辺の土地の文を入れる", "灰の結社の監視が行き届いた村" in text and "丘陵に囲まれた土地" in text
      and "暗い" not in text, text)
INCIDENTS = {"events": [
    {"id": 1, "area": "1", "kind": "bandits", "status": "ended", "end_by": "player", "started": 80,
     "ended": 110, "text": {"name": "盗賊の横行"}},
    {"id": 2, "area": "7", "kind": "plague", "status": "active", "stage": 2, "started": 120,
     "stage_day": 125, "text": {"name": "疫病"}},
    {"id": 3, "area": "0", "kind": "fire", "status": "ended", "end_by": "natural", "started": 10,
     "ended": 40}]}
lines = material.incidents_since(INCIDENTS, 100, towns)
check("前回からの出来事だけ", lines == ["- 東の村の盗賊の横行は冒険者が片付けた", "- 北の巡礼町で疫病（悪化）"],
      lines)
GOOD_EVENTS = {"events": [
    {"area": "7", "kind": "boom", "status": "active", "stage": 2, "stage_name": "過熱", "good": True,
     "started": 120, "text": {"name": "銀の熱"}},
    {"area": "1", "kind": "festival", "status": "ended", "end_by": "natural", "good": True,
     "started": 90, "ended": 130, "text": {"name": "灯の祭"}}]}
lines = material.incidents_since(GOOD_EVENTS, 100, towns)
check("良い出来事は段の名前と「落ち着いた」で読む（338 版2）",
      lines == ["- 北の巡礼町で銀の熱（良い出来事・過熱）", "- 東の村の灯の祭は落ち着いた"], lines)

# ================================================================ 流れ
class Area:
    def __init__(self, area_id, name, residents=()):
        self.id = area_id
        self.name = name
        self.nodes = {}
        self.resident_npcs = list(residents)
        self.adventurer_npcs = []


class World:
    def __init__(self):
        self.areas = {"0": Area("0", "中央の町", ["30"]), "1": Area("1", "東の村", ["31"]),
                      "7": Area("7", "北の巡礼町", ["37"])}
        self.characters = {}
        self.days_elapsed = 100


class Player:
    def __init__(self):
        self.name = "検査の主人公"
        self.profile = "白の教団に故郷を追われた元・拾い屋。"
        self.current_area = "0"
        self.area_history = {"7": {"lawfulness": -160, "achievements": []},
                             "1": {"lawfulness": 10, "achievements": ["東の村の遺跡で遺物を回収した"]}}


class Character:
    def __init__(self, character_id, name):
        self.id = character_id
        self.name = name


class PhaseSpec:
    """ゲームの `PhaseSpec` の代わり（`ui.cls_of` は `__main__` から引く。この検査が `__main__`）。"""

    def __init__(self, cls_name, args):
        self.cls_name, self.args = cls_name, list(args)

    def to_dict(self):
        return {"cls_name": self.cls_name, "args": list(self.args)}


class InstantaleApp:
    def __init__(self):
        self.world = World()
        self.player = Player()
        self.save_data_dict = json.loads(json.dumps(SAVE))
        self.world_dict = self.save_data_dict
        self.party = ["player"]
        self.texts = []

    def add_text(self, text):
        self.texts.append(text)

    def elapse_days(self, days):
        self.world.days_elapsed += days
        self.save_data_dict["world_data"]["days_elapsed"] = self.world.days_elapsed


class FakeCtx:
    def __init__(self):
        self.out_dir = OUT_DIR
        self.state_dir = STATE_DIR
        self.hooks = {}
        self.errors = []
        self.logs = []
        self.generation = object()
        self.ready = []

    _mod = None

    def out_path(self, *parts):
        path = os.path.join(self.out_dir, *parts)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        return path

    def logger(self, name, **kw):
        return ml.ModContext.logger(self, name, **kw)

    def state_path(self, *parts):
        path = os.path.join(self.state_dir, *parts)
        os.makedirs(os.path.dirname(path) if os.path.splitext(path)[1] else path, exist_ok=True)
        return path

    def log(self, msg, level="INFO"):
        self.logs.append((level, msg))

    def log_exc(self, msg):
        import traceback
        self.errors.append(msg + "\n" + traceback.format_exc())

    def write_json(self, path, data, *, indent=1):
        return ml.write_json(path, data, indent=indent, report=self.log_exc)

    def read_json(self, path, default=None):
        return ml.read_json(path, default, report=self.log_exc)

    def resolve(self, target):
        return None, None, object()

    def wrap(self, target, **kw):
        def decorator(func):
            self.hooks.setdefault(target, []).append(func)
            return func
        return decorator

    def hook(self, target):
        return self.hooks[target][-1]

    def on_ready(self, fn):
        self.ready.append(fn)

    def superseded(self):
        return False


# ---- LLM の代わり
class FakeModel:
    fields = ()

    def __init__(self, **data):
        missing = [name for name in self.fields if name not in data]
        if missing:
            raise ValueError("missing {}".format(missing))
        for name in self.fields:
            setattr(self, name, data[name])

    @classmethod
    def model_validate(cls, data):
        return cls(**data)

    def model_dump(self):
        return {name: getattr(self, name) for name in self.fields}


def create_model(model_name, /, __base__=FakeModel, **fields):
    """pydantic と同じく型の名前は位置だけで受ける（項目に name を持てる）。"""
    return type(model_name, (__base__,), {"fields": tuple(__base__.fields) + tuple(fields)})


Result = create_model("Result", summary=(str, ...))
MANAGER = types.ModuleType("scripts.llm.llm_manager")
MANAGER.create_model = create_model
sys.modules[MANAGER.__name__] = MANAGER

ASKED = []
ANSWERS = {}


#: 住人の立場の答え（頼みに並んだ id ごと。書いていない id は一員）。
PEOPLE_ROLES = {"38": ("dissenter", ""), "39": ("indifferent", ""), "40": ("other", "灰の結社")}


def people_answer(message):
    ids = re.findall(r"^- id (\S+):", message[1]["content"], re.M)
    return {"people": [{"id": npc_id, "reason": "人物紹介から", "role": PEOPLE_ROLES.get(npc_id, ("member", ""))[0],
                        "faction": PEOPLE_ROLES.get(npc_id, ("member", ""))[1]} for npc_id in ids]}


def fake_ask(ctx, manager_name, message, *, timeout, structure=None, **kw):
    ASKED.append((manager_name, message))
    answer = ANSWERS.get(manager_name)
    return answer(message) if callable(answer) else answer


llm.ask = fake_ask
APP = None
ui.find_app = lambda *a, **k: APP
ui.party_member_ids = lambda app: [m for m in getattr(app, "party", []) if m != "player"]

EXTRACT_ANSWER = {
    "factions": [{"name": "白の教団", "kind": "宗教組織", "note": "遺物を罪とみなす", "evidence": "遺物を罪の証として排除する"},
                 {"name": "灰の結社", "kind": "結社", "note": "古代を崇める", "evidence": "灰の結社は古代文明を崇拝し"}],
    "towns": [{"town_id": "1", "faction": "灰の結社", "evidence": "灰の結社の監視が行き届いた村"},
              {"town_id": "7", "faction": "白の教団", "evidence": "白の教団が管理する巡礼の町"}],
    "relations": [{"a": "白の教団", "b": "灰の結社", "stage": -2, "evidence": "白の教団と敵対する"}],
    "peaceful": "false"}
#: 最初の立ち位置の答え。途中導入（調整）では adjust を、新しいキャラ（出自）では stage を読む。
STANDING_ANSWER = {"standings": [
    {"faction": "白の教団", "stage": -2, "adjust": 0, "title": "異端の拾い屋", "reason": "北の巡礼町で追われている"},
    {"faction": "灰の結社", "stage": 1, "adjust": 1, "title": "腕利きの拾い屋", "reason": "東の村で遺物を回収した"}]}


def fresh(enable=True):
    global APP, module, rules
    module = load_package()
    rules = sys.modules[MOD_NAME + ".rules"]
    for attr in (module.STORE_ATTR, module.SETTINGS_STORE_ATTR):
        if hasattr(sys, attr):
            delattr(sys, attr)
    if os.path.isdir(STATE_DIR):
        shutil.rmtree(STATE_DIR)
    modnpc.registry().clear()
    talk_summary.reset()
    del ASKED[:]
    ANSWERS.clear()
    ANSWERS.update({module.MANAGER_EXTRACT: EXTRACT_ANSWER, module.MANAGER_STANDING: STANDING_ANSWER,
                    module.MANAGER_PEOPLE: people_answer})
    APP = InstantaleApp()
    ctx = FakeCtx()
    module.apply(ctx)
    worker = module._store()["worker"]
    worker.enqueue = lambda job: worker.run(job)
    if enable:
        settings = getattr(sys, module.SETTINGS_STORE_ATTR)
        from instantale_modloader.state import world_key
        record = settings.load(world_key(APP))
        record["ENABLED"] = True
        record["TREND_SPREAD_DAYS"] = 0     # 間隔を決まった日数で見る（ばらつきは「情勢の間隔のばらつき」で見る）
        settings.save(world_key(APP))
    return module, ctx, APP


def ledger_of(app):
    store = module._store()["worlds"]
    return store.load(store.playthrough(app), fresh=True)


def notes_fn():
    for entry in modnpc.registry().values():
        for layer in entry.get("layers") or []:
            if layer.get("owner") == module.OWNER and callable(layer.get("notes")):
                return layer["notes"]
    return None


SYSTEM = {"role": "system", "content": "会話を要約して下さい。"}
SPOKE = [{"role": "user", "content": "<行動: 話しかける>"},
         {"role": "user", "content": "古代の遺産は素晴らしい。灰の結社の研究に協力したい"}]
SENT = []


def close(ctx, character, answer):
    """会話を閉じる。要約の送信は足した項目に answer を入れて返す。"""
    def game_send(manager_name, message, structure, **kw):
        SENT.append((manager_name, message, structure))
        data = {"summary": "要約"}
        for name in structure.fields:
            if name != "summary":
                data[name] = answer.get(name)
        return structure(**data)

    def resolver(player, messages_, life_log, character_instance, emotion, worldview):
        # 同じ送信の境目には 339 の世界の生成の包みと刺客の書き換え（`llm.wrap_outgoing`）も載る。要約の窓口の包みを呼ぶ
        summary_hook = next(fn for fn in ctx.hooks[talk_summary.SEND_TARGET]
                            if getattr(fn, "__module__", "") == talk_summary.__name__)
        return summary_hook(game_send, "conversation_resolver", [dict(SYSTEM)] + SPOKE, Result)

    return ctx.hook(talk_summary.RESOLVER_TARGET)(resolver, APP.player, SPOKE, {}, character, "", "")


print("[窓] 中身を組む")
written, changed = rules.record_moves(
    {"history": []}, [("f2", 40, 50, "gain"), ("f1", -80, -85, "echo")], day=3, kind="talk", reason="r")
check("段階が変わらない動きは changed にならない", (written, changed) == (2, False))
book = {"history": []}
written, changed = rules.record_moves(book, [("f2", 50, 62, "gain"), ("f2", 62, 62, "capped")], day=3,
                                      kind="incident", incident="ゼニスの疫病")
check("段階が変わる動きと、動かなかった回を書かないこと", (written, changed) == (1, True)
      and book["history"][0]["incident"] == "ゼニスの疫病")
book = {"history": []}
for i in range(rules.HISTORY_LIMIT + 5):
    rules.add_history(book, {"day": i})
check("来歴は新しい40件まで", len(book["history"]) == rules.HISTORY_LIMIT and book["history"][0]["day"] == 5)
LEDGER = {
    "status": rules.READY, "peaceful": False,
    "factions": [{"id": "f1", "name": "白翼騎士団", "kind": "軍事組織", "note": "異端を許さない。"},
                 {"id": "f2", "name": "黄金商会", "kind": "商人の連合", "note": ""},
                 {"id": "f3", "name": "霧の船団", "kind": "海賊", "note": ""}],
    "towns": {"0": {"faction": "f1"}, "1": {"faction": "f2"}, "2": {"faction": "f3"}},
    "relations": {"f1|f3": {"stage": -2}, "f1|f2": {"stage": 1}, "f2|f3": {"stage": -1}},
    "standing": {"f1": {"score": -21}, "f2": {"score": 40, "title": "有能な傭兵", "title_stage": 1},
                 "f3": {"score": -85, "locked": True, "title": "裏切り者", "title_stage": -1}},
    "events": [
        {"day": -400, "a": "f1", "b": "f2", "direction": 1, "before": 0, "after": 0, "rumor": "古い噂"},
        {"day": -30, "a": "f1", "b": "f3", "direction": -1, "before": -2, "after": -2, "rumor": "没収したらしい"},
        {"day": 50, "a": "f2", "b": "f3", "direction": 1, "before": -1, "after": -1, "rumor": "歩み寄ったらしい"},
        {"day": 60, "a": "f1", "b": "f2", "direction": 1, "before": 0, "after": 1, "rumor": "援護したらしい"}],
    "history": [
        {"day": 40, "faction": "f2", "before": 15, "after": 25, "kind": "incident", "incident": "オーラムの疫病"},
        {"day": 40, "faction": "f3", "before": -60, "after": -65, "kind": "echo", "by": "f2"},
        {"day": 97, "faction": "f3", "before": -45, "after": -85, "kind": "talk", "reason": "掟を嘲った"},
        {"day": 98, "faction": "f3", "before": -85, "after": -85, "kind": "reconcile"}]}
model = view.build(LEDGER, day=100, here="0", area_names={"0": "ヴァルガルド", "1": "オーラム", "2": "ミストル"},
                   laws={"0": -5})
cards = {c["name"]: c for c in model["cards"]}
check("現在地と影響下の勢力", model["here"] == "ヴァルガルド（白翼騎士団の影響下）", model["here"])
check("札の段階・影響下の街・他勢力との関係（悪い順）", cards["白翼騎士団"]["stage"] == -1
      and cards["白翼騎士団"]["towns"] == ["ヴァルガルド"]
      and cards["白翼騎士団"]["relations"] == [("霧の船団", -2), ("黄金商会", 1)], cards["白翼騎士団"])
check("手配の上限に届いていれば手配の行", cards["白翼騎士団"]["cautions"] == [view.WANTED_TEXT])
check("縛りの行。呼び名は付けた段階と違えば出さない", cards["霧の船団"]["cautions"] == [view.LOCKED_TEXT]
      and cards["霧の船団"]["title"] == "" and cards["黄金商会"]["title"] == "有能な傭兵", cards["霧の船団"])
low = view.build(dict(LEDGER, standing=dict(LEDGER["standing"], f1={"score": -50})), day=100,
                 area_names={}, laws={"0": -5})
check("上限に届く前は手配の行を出さない", low["cards"][0]["cautions"] == [], low["cards"][0])
rumors = model["rumors"]
check("噂は新しい順で、1年より前は出さない", [r["text"] for r in rumors]
      == ["援護したらしい", "歩み寄ったらしい", "没収したらしい"], [r["text"] for r in rumors])
check("段が変わった噂は関係を段の色で", rumors[0]["when"] == "40日前" and rumors[0]["follow"]
      == [("これにより、白翼騎士団と黄金商会は", None), ("友好関係", 1), ("となったようだ。", None)], rumors[0])
check("1回目で段が変わらないときは兆し", rumors[1]["follow"]
      == [("これにより、黄金商会と霧の船団の関係には改善の兆しが見えるようだ。", None)], rumors[1])
check("端にいるときは溝が深まった", rumors[2]["follow"]
      == [("これにより、白翼騎士団と霧の船団の溝は一層深まったようだ。", None)], rumors[2])
history = model["history"]
check("来歴は新しい順", [h["when"] for h in history] == ["2日前", "3日前", "60日前", "60日前"], history)
check("縛りが解けた来歴", history[0]["pieces"] == [("説得が実り、霧の船団の敵意に和らぐ余地が生まれたようだ。", None)])
check("会話で段階が変わった来歴は段階の色と理由", history[1]["pieces"]
      == [("会話により、霧の船団の態度は", None), ("敵対", -2), ("となった。", None)]
      and history[1]["reason"] == "掟を嘲った", history[1])
check("響きの来歴", history[2]["pieces"] == [("黄金商会への働きが伝わり、霧の船団の態度は", None), ("敵対", -2),
                                            ("となった。", None)], history[2])
check("出来事の来歴（段階が変わったとき）", history[3]["pieces"]
      == [("オーラムの疫病を解決したことで、黄金商会の態度は", None), ("友好", 1), ("となった。", None)], history[3])
check("段階が変わらなければ評価が上がった・下がった",
      view.pieces_of(LEDGER, {"faction": "f2", "before": 40, "after": 45, "kind": "talk"})
      == [("会話により、黄金商会の評価が上がった。", None)])
check("作っている途中・勢力が無い・作れなかったときは一文", [view.build({"status": st})["message"]
                                                     for st in (None, rules.NONE, rules.FAILED)]
      == [view.MESSAGES[None], view.MESSAGES[rules.NONE], view.MESSAGES[rules.FAILED]])

print("[流れ] 有効にするまで何もしない")
module, ctx, app = fresh(enable=False)
for fn in ctx.ready:
    fn()
check("台帳を作らない", not ASKED and ledger_of(app).get("status") is None, ASKED)
check("会話に足さない", notes_fn()({"app": app, "npc_id": "37"}) is None)
check("左下の一覧に行を出さない（登録はしてあり、出すかはその場で聞く）", module.ICON_KEY in hudicons.keys()
      and module.ICON_KEY not in [e["key"] for e in hudicons.menu_entries(app)], hudicons.keys())

print("[流れ] 読み込みで台帳を作る")
module, ctx, app = fresh()
for fn in ctx.ready:
    fn()
book = ledger_of(app)
check("抽出 → 初回の判定の順に2回聞く",
      [m for m, _ in ASKED] == [module.MANAGER_EXTRACT, module.MANAGER_STANDING], [m for m, _ in ASKED])
check("台帳ができる", book["status"] == rules.READY and book["towns"]["7"]["faction"] == "f1", book)
rows = [e for e in hudicons.menu_entries(app) if e["key"] == module.ICON_KEY]
check("有効な世界では左下の一覧に「勢力と情勢」の行", rows and rows[0]["label"] == "勢力と情勢"
      and rows[0]["images"][0].endswith("icon_flag.png"), rows)
check("途中導入は土台に調整（教団は手配 -160 で -80 のまま、結社は功績1件 +10 から +1 段階で 40）",
      book["standing"]["f1"]["score"] == -80 and book["standing"]["f2"]["score"] == 40
      and book["standing"]["f2"]["baseline"] == 10 and book["standing"]["f2"]["adjust"] == 1,
      book["standing"])
check("情勢の起点は作った日", book["trend"]["last_day"] == 100)
standing_prompt = ASKED[1][1][1]["content"]
check("調整の材料（経歴・土台・その街の功績と手配）", "白の教団に故郷を追われた" in standing_prompt
      and "手配度 -160" in standing_prompt and "遺物を回収した" in standing_prompt
      and "土台の段階: 敵対" in standing_prompt, standing_prompt)
check("調整の頼みを使う", ASKED[1][1][0]["content"] == material.ADJUST_SYSTEM)
del ASKED[:]
for fn in ctx.ready:
    fn()
check("作った後は台帳を聞き直さず、勢力の街ごとに住人の立場を1回ずつ聞く",
      [m for m, _ in ASKED] == [module.MANAGER_PEOPLE, module.MANAGER_PEOPLE], [m for m, _ in ASKED])
people_prompt = ASKED[1][1][1]["content"]
check("立場の材料は街の勢力と住人の人物紹介（名簿に無い店の主も生まれた街で拾う）",
      "この街で力を持つ勢力: 白の教団" in people_prompt and "- id 38: 酒場の主（inn）" in people_prompt
      and "教団の説教を嫌う" in people_prompt and "中央の町の宿屋" not in people_prompt, people_prompt)
book = ledger_of(app)
check("住人の立場を台帳に書く", {k: (v["role"], v["faction"]) for k, v in book["people"].items()}
      == {"31": ("member", "f2"), "37": ("member", "f1"), "38": ("dissenter", "f1"),
          "39": ("indifferent", "f1"), "40": ("other", "f2")}, book["people"])
del ASKED[:]
for fn in ctx.ready:
    fn()
check("決めた後は聞かない", not ASKED, ASKED)


print("[流れ] 会話の構えと噂")
notes = notes_fn()
text = notes({"app": app, "npc_id": "37", "site": "conversation_starter"}) or ""
check("白の教団の町の住人は敵対", "白の教団に属する者" in text and "「異端の拾い屋」" in text, text)
text = notes({"app": app, "npc_id": "31"}) or ""
check("灰の結社の村の住人は好意", "灰の結社に属する者" in text and "好意" in text, text)
check("勢力の無い町の住人には足さない", notes({"app": app, "npc_id": "30"}) is None)
app.player.current_area = "7"
text = notes({"app": app, "npc_id": "99"}) or ""
check("住まいの分からない相手は、いま居る街の勢力に従う住人", "この街を治める白の教団の者たちは、検査の主人公を敵とみなしている" in text
      and "一員ではない" in text, text)
text = notes({"app": app, "npc_id": "38"}) or ""
check("面従腹背の住人は、内心の反感と、勢力の敵意を嫌う理由にしないこと", "表向き白の教団に従っているが" in text
      and "嫌う理由にはならない" in text and "本音を出さない" in text and "属する者" not in text, text)
check("無関心の住人には足さない", notes({"app": app, "npc_id": "39"}) is None)
text = notes({"app": app, "npc_id": "40"}) or ""
check("他の勢力寄りは、その勢力からの見られ方を表に出さない形で", "心は灰の結社の側にあり" in text
      and "灰の結社に属する者として" in text and "白の教団" not in text, text)
worlds = module._store()["worlds"]
with worlds.lock:
    worlds.load(worlds.playthrough(app))["people"]["40"]["open"] = True
    worlds.save(worlds.playthrough(app))
text = notes({"app": app, "npc_id": "40"}) or ""
check("公然と他の勢力の者なら、その勢力の者として周りも知っている形で（呼び名も）",
      "灰の結社の者としてこの街にいて、周りもそれを知っている" in text and "心は" not in text
      and "「腕利きの拾い屋」" in text, text)
with worlds.lock:
    worlds.load(worlds.playthrough(app))["people"]["40"]["open"] = False
    worlds.save(worlds.playthrough(app))
app.party.append("37")
check("仲間には足さない", notes({"app": app, "npc_id": "37"}) is None)
app.party.remove("37")
check("MOD の NPC には足さない", notes({"app": app, "npc_id": "mod:330_real_estate:keeper"}) is None)

print("[流れ] 要約に相乗りして見られ方を動かす")
close(ctx, Character("31", "東の村の番人"), {"faction_change": 2, "faction_reason": "灰の結社に協力的"})
book = ledger_of(app)
check("送信は1回で勢力の項目を足す", len(SENT) == 1 and "faction_change" in SENT[0][2].fields
      and "灰の結社に属している" in SENT[0][1][0]["content"], SENT[-1][2].fields)
check("灰の結社 +10・敵対する白の教団 -5", book["standing"]["f2"]["score"] == 50
      and book["standing"]["f1"]["score"] == -85, book["standing"])
close(ctx, Character("31", "東の村の番人"), {"faction_change": 2, "faction_reason": "また"})
check("同じ日に2回目は上がらない", ledger_of(app)["standing"]["f2"]["score"] == 50)
close(ctx, Character("31", "東の村の番人"), {"faction_change": -1, "faction_reason": "侮辱"})
check("下がる方は効く", ledger_of(app)["standing"]["f2"]["score"] == 42)
check("-81 以下になった勢力に印", ledger_of(app)["standing"]["f1"].get("locked") is True)
history = ledger_of(app)["history"]
check("会話の来歴（上がった・響き・下がった。1日1回で止まった回は書かない）",
      [(h["kind"], h["faction"], h["before"], h["after"]) for h in history]
      == [("talk", "f2", 40, 50), ("echo", "f1", -80, -85), ("talk", "f2", 50, 42)], history)
check("会話の来歴に理由、響きに上がった勢力", history[0]["reason"] == "灰の結社に協力的"
      and history[1]["by"] == "f2" and history[1]["reason"] == "", history)
app.world.days_elapsed += 1
app.player.area_history["1"]["lawfulness"] = -5
close(ctx, Character("31", "東の村の番人"), {"faction_change": 2, "faction_reason": "協力的"})
check("手配されている街の勢力は緊張より上へ上がらない（もう上にいれば動かず、響きも無い）",
      ledger_of(app)["standing"]["f2"]["score"] == 42 and ledger_of(app)["standing"]["f1"]["score"] == -85,
      ledger_of(app)["standing"])
app.player.area_history["1"]["lawfulness"] = 10
app.world.days_elapsed -= 1
del SENT[:]
close(ctx, Character("30", "中央の町の宿屋"), {"faction_change": 2})
check("勢力の無い相手の要約には足さない", SENT[0][2] is Result)
before = {k: v["score"] for k, v in ledger_of(app)["standing"].items()}
for npc_id, name in (("38", "酒場の主"), ("39", "古物商"), ("99", "通りすがり")):
    del SENT[:]
    close(ctx, Character(npc_id, name), {"faction_change": -2})
    check("一員でない相手（{}）との会話は見られ方を動かさない".format(name), SENT[0][2] is Result)
check("動いていない", {k: v["score"] for k, v in ledger_of(app)["standing"].items()} == before)
app.world.days_elapsed += 1
del SENT[:]
close(ctx, Character("40", "結社の使い"), {"faction_change": 1, "faction_reason": "結社の理念に共感"})
check("他の勢力寄りの相手は、その勢力の者として判定する", "灰の結社に属している" in SENT[0][1][0]["content"]
      and ledger_of(app)["standing"]["f2"]["score"] == before["f2"] + 5, ledger_of(app)["standing"])
app.world.days_elapsed -= 1

print("[規則] 住人の立場")
people_ledger = {"factions": [{"id": "f1", "name": "白の教団"}, {"id": "f2", "name": "灰の結社"}],
                 "towns": {"7": {"faction": "f1"}}, "standing": {"f1": {"score": -50}}}
rows, problems = rules.read_people({"people": [
    {"id": "1", "role": "member", "faction": ""},
    {"id": "2", "role": "member", "faction": "灰の結社"},
    {"id": "3", "role": "other", "faction": "貴族"},
    {"id": "4", "role": "面従腹背", "faction": ""},
    {"id": "9", "role": "member", "faction": ""},
    {"id": "5", "role": "wizard", "faction": ""}]}, people_ledger, "7", ["1", "2", "3", "4", "5", "6"])
check("別の勢力の一員と答えたら他の勢力寄り", rows["2"]["role"] == "other" and rows["2"]["faction"] == "f2")
check("無い勢力の他の勢力寄りは従う住人", rows["3"]["role"] == "follower" and rows["3"]["faction"] == "f1")
check("日本語の立場も読む", rows["4"]["role"] == "dissenter")
check("一員と答えて勢力が別なら公然の他の勢力寄り", rows["2"].get("open") is True)
by_field, _ = rules.read_people({"people": [
    {"id": "1", "role": "follower", "faction": "灰の結社", "open": ""},
    {"id": "2", "role": "other", "faction": "灰の結社", "open": "true"},
    {"id": "3", "role": "member", "faction": "灰の結社", "open": "false"},
    {"id": "4", "role": "member", "faction": "白の教団", "open": ""}]}, people_ledger, "7", ["1", "2", "3", "4"])
check("勢力の欄が別の勢力なら、役が何でも他の勢力寄り（役より勢力の欄を信じる）",
      [(by_field[i]["role"], by_field[i]["faction"], by_field[i].get("open")) for i in "1234"]
      == [("other", "f2", False), ("other", "f2", True), ("other", "f2", False), ("member", "f1", None)], by_field)
check("公然の他の勢力寄りは会話で見られ方を動かす立場", rules.OTHER_OPEN in rules.JUDGING_ROLES)
check("公然の他の勢力寄りは中立でも、その勢力の者だと足す", rules.stance_text(
    dict(people_ledger, standing={"f2": {"score": 0}}), "f2", "主人公", rules.OTHER_OPEN)
      == "あなたは灰の結社の者としてこの街にいて、周りもそれを知っている。")
check("読めない立場と答えに無い住人は従う住人（聞き直さない）", rows["5"]["role"] == "follower"
      and rows["6"]["role"] == "follower" and rows["6"].get("missing") is True)
check("頼んでいない id は書かない", "9" not in rows and any("unknown id" in p for p in problems), problems)
check("決めていない住人は勢力の街だけ数える",
      rules.unjudged({"towns": {"7": {"faction": "f1"}}, "people": {"1": {"town": "7"}}}, {"7": ["1", "2"], "0": ["3"]})
      == {"7": ["2"]})
check("住まいが変わった住人は決め直す",
      rules.unjudged({"towns": {"7": {"faction": "f1"}, "1": {"faction": "f2"}},
                      "people": {"1": {"town": "1", "role": "member"}}}, {"7": ["1"]}) == {"7": ["1"]})
check("従う住人は中立なら足さない", rules.stance_text(dict(people_ledger, standing={"f1": {"score": 0}}),
                                             "f1", "主人公", rules.FOLLOWER) == "")
check("面従腹背は中立でも内心を足す", "表向き白の教団に従っているが" in rules.stance_text(
    dict(people_ledger, standing={"f1": {"score": 0}}), "f1", "主人公", rules.DISSENTER))
text = rules.stance_text(dict(people_ledger, standing={"f1": {"score": 50}}), "f1", "主人公", rules.DISSENTER)
check("勢力が好意的なら、それを好く理由にしない", "好意を持つ理由にはならない" in text, text)

print("[流れ] 337 と同じ要約に乗る")
affinity_dir = [n for n in os.listdir(MODS_DIR) if n.endswith("_conversation_affinity")][0]
spec = importlib.util.spec_from_file_location(
    "affinity_mod", os.path.join(MODS_DIR, affinity_dir, "conversation_affinity.py"))
affinity = importlib.util.module_from_spec(spec)
spec.loader.exec_module(affinity)
affinity.apply(ctx)
del SENT[:]
keeper = Character("31", "東の村の番人")
keeper.relationship = {"player": {"affinity": 10}}
app.world.days_elapsed += 1
close(ctx, keeper, {"faction_change": 1, "faction_reason": "a", "affinity_change": 1,
                    "affinity_reason": "b"})
check("2本乗っても送信は1回", len(SENT) == 1 and {"faction_change", "affinity_change"}
      <= set(SENT[0][2].fields), SENT[0][2].fields)
check("両方が動く", ledger_of(app)["standing"]["f2"]["score"] == 47
      and keeper.relationship["player"]["affinity"] == 12,
      (ledger_of(app)["standing"], keeper.relationship))
talk_summary.withdraw(affinity.OWNER)

print("[流れ] 日数が進む")
incidents_dir = os.path.join(STATE_DIR, "town_incidents")
os.makedirs(incidents_dir, exist_ok=True)
store = module._store()["worlds"]
key = store.playthrough(app)
with io.open(os.path.join(incidents_dir, key + ".json"), "w", encoding="utf-8") as fh:
    json.dump(INCIDENTS, fh, ensure_ascii=False)
ANSWERS[module.MANAGER_TREND] = {"happened": "true", "a": "灰の結社", "b": "白の教団", "direction": "+1",
                                 "rumor": "白の教団と灰の結社が遺跡の扱いで話し合いを始めたらしい", "reason": "疫病"}
before = ledger_of(app)["standing"]["f2"]["score"]
del ASKED[:]
ctx.hook(module.DAYS_TARGET)(lambda self, d: self.elapse_days(d), app, 10)
book = ledger_of(app)
check("片付けた出来事の街の勢力が上がる", book["standing"]["f2"]["score"] == before + 10, book["standing"])
check("出来事の来歴に街と出来事の名前", book["history"][-2]["kind"] == "incident"
      and book["history"][-2]["incident"] == "街の盗賊の横行" and book["history"][-1]["kind"] == "echo",
      book["history"][-2:])
check("終わった出来事を数えた印に残す（進行中は残さない）",
      book["trend"]["incidents"] == {"1": "player", "3": "natural"}, book["trend"]["incidents"])
ctx.hook(module.DAYS_TARGET)(lambda self, d: self.elapse_days(d), app, 1)
check("二度は上げない", ledger_of(app)["standing"]["f2"]["score"] == before + 10)
check("間隔の前は情勢を進めない", not any(m == module.MANAGER_TREND for m, _ in ASKED))
ctx.hook(module.DAYS_TARGET)(lambda self, d: self.elapse_days(d), app, 30)
book = ledger_of(app)
trend_prompt = [msg for m, msg in ASKED if m == module.MANAGER_TREND]
check("間隔が来たら情勢を1回", len(trend_prompt) == 1, [m for m, _ in ASKED])
check("情勢の材料に前回からの出来事", trend_prompt and "北の巡礼町で疫病（悪化）" in trend_prompt[0][1]["content"],
      trend_prompt[0][1]["content"] if trend_prompt else "")
check("設定の関係は1回では動かない", book["relations"]["f1|f2"]["stage"] == -2
      and book["relations"]["f1|f2"]["push"] == 1, book["relations"])
check("起きたことを記録して起点を進める", book["events"][-1]["rumor"].startswith("白の教団と灰の結社")
      and book["trend"]["last_day"] == 142, (book["events"], book["trend"]))
text = notes({"app": app, "npc_id": "30"}) or ""
check("噂を会話に足す", "【各地の噂】白の教団と灰の結社が" in text, text)
app.world.days_elapsed += 200
check("日が経てば噂は消える", "各地の噂" not in (notes({"app": app, "npc_id": "30"}) or ""))
ANSWERS[module.MANAGER_TREND] = {"happened": "false", "a": "", "b": "", "direction": "0",
                                 "rumor": "", "reason": ""}
ctx.hook(module.DAYS_TARGET)(lambda self, d: self.elapse_days(d), app, 1)
check("何も起きない回", len(ledger_of(app)["events"]) == 1 and ledger_of(app)["trend"]["last_day"] == 343)
ANSWERS[module.MANAGER_TREND] = {"happened": "遺跡の扱いをめぐって口論になったが、最後は握手して別れた。",
                                 "a": "灰の結社", "b": "白の教団", "direction": "+1",
                                 "rumor": "灰の結社と白の教団が握手したらしい", "reason": "x"}
ctx.hook(module.DAYS_TARGET)(lambda self, d: self.elapse_days(d), app, 30)
check("happened に出来事の文を書いた答えも起きた回として読む", len(ledger_of(app)["events"]) == 2
      and ledger_of(app)["events"][-1]["rumor"].startswith("灰の結社と白の教団"), ledger_of(app)["events"])

print("[流れ] 情勢の間隔のばらつき")
module, ctx, app = fresh()
from instantale_modloader.state import world_key  # noqa: E402
settings = getattr(sys, module.SETTINGS_STORE_ATTR)
settings.load(world_key(app))["TREND_SPREAD_DAYS"] = 10
settings.save(world_key(app))
ROLLS, ROLLED = [7, 3, 9], []


class FixedRng(object):
    def randint(self, low, high):
        ROLLED.append((low, high))
        return ROLLS.pop(0) if ROLLS else low


module.RNG = FixedRng()
ANSWERS[module.MANAGER_TREND] = {"happened": "true", "a": "灰の結社", "b": "白の教団", "direction": "+1",
                                 "rumor": "噂", "reason": "x"}
notes_fn()({"app": app, "npc_id": "30"})
check("台帳を作るときに足す日数を引く", ledger_of(app)["trend"]["extra"] == 7 and ROLLED == [(0, 10)],
      (ledger_of(app)["trend"], ROLLED))
start = ledger_of(app)["trend"]["last_day"]


def advance(days):
    del ASKED[:]
    ctx.hook(module.DAYS_TARGET)(lambda self, d: self.elapse_days(d), app, days)
    return any(m == module.MANAGER_TREND for m, _ in ASKED)


check("間隔＋足した日数の前は動かない", not advance(36))
check("間隔＋足した日数で動く", advance(1) and ledger_of(app)["trend"]["last_day"] == start + 37)
check("動いたら足す日数を引き直す", ledger_of(app)["trend"]["extra"] == 3 and len(ROLLED) == 2, ROLLED)
check("引き直した日数で次を待つ", not advance(32) and advance(1))
book = ledger_of(app)
book["trend"]["extra"] = 9
settings.load(world_key(app))["TREND_SPREAD_DAYS"] = 2
settings.save(world_key(app))
check("ばらつきを狭めたら、引いてある日数も狭めた幅で見る", not advance(31) and advance(1))
book = ledger_of(app)
del book["trend"]["extra"]
settings.load(world_key(app))["TREND_SPREAD_DAYS"] = 10
settings.save(world_key(app))
ROLLS[:] = [5]
check("足す日数を持たない台帳（版4まで）は日数が進んだときに引く", not advance(1)
      and ledger_of(app)["trend"]["extra"] == 5, ledger_of(app)["trend"])
check("例外が無い（ばらつき）", not ctx.errors, ctx.errors)
module.RNG = __import__("random").Random()

print("[流れ] 読み込みの包みが拾えなかったとき")
module, ctx, app = fresh()
from instantale_modloader import choices  # noqa: E402
provider = choices._store()["providers"].get(module.OWNER)
check("選択肢の組み直しに登録した", provider is not None and callable(provider.get("refresh")))
provider["refresh"](app, [])
check("組み直しで台帳を作る", ledger_of(app)["status"] == rules.READY
      and [m for m, _ in ASKED] == [module.MANAGER_EXTRACT, module.MANAGER_STANDING])
provider["refresh"](app, [])
check("同じ周回では二度確かめない", len(ASKED) == 2)

print("[流れ] 手で直した台帳")
# ゲームを閉じて台帳を手で直し、BOM 付きで保存した形。組の書き漏らしは情勢で落ちていた
store = module._store()["worlds"]
key = store.playthrough(app)
book = json.loads(json.dumps(ledger_of(app)))
book["factions"].append({"id": "f3", "name": "黒鉄の商会", "kind": "商人の組合", "note": "", "evidence": "",
                         "source": "manual"})
book["relations"] = {"f3|f1": {"stage": -2, "push": 0, "from_setting": True, "evidence": "", "updated_day": 0}}
book["towns"]["9"] = {"faction": "f9", "evidence": ""}
with io.open(store.path(key), "wb") as fh:
    fh.write(b"\xef\xbb\xbf" + json.dumps(book, ensure_ascii=False, indent=1).encode("utf-8"))
with store.lock:
    store._buckets.pop(key, None)          # 起動し直した形（読んだ控えを持っていない）
del ASKED[:]
notes_fn()({"app": app, "npc_id": "30"})
book = ledger_of(app)
check("BOM 付きでも作り直さない", book["status"] == rules.READY
      and module.MANAGER_EXTRACT not in [m for m, _ in ASKED]
      and [f["id"] for f in book["factions"]] == ["f1", "f2", "f3"], ([m for m, _ in ASKED], book["factions"]))
check("読み込みで組と見られ方を埋めて書き戻す", sorted(book["relations"]) == ["f1|f2", "f1|f3", "f2|f3"]
      and book["relations"]["f1|f3"]["stage"] == -2 and book["standing"]["f3"]["score"] == 0
      and "9" not in book["towns"], (book["relations"], book["standing"], book["towns"]))
with io.open(store.path(key), encoding="utf-8") as fh:
    check("書き戻したファイルは BOM なしで揃っている", sorted(json.load(fh)["relations"]) == ["f1|f2", "f1|f3", "f2|f3"])
ANSWERS[module.MANAGER_TREND] = {"happened": "true", "a": "黒鉄の商会", "b": "灰の結社", "direction": "-1",
                                 "rumor": "黒鉄の商会が灰の結社との取引を絞ったらしい", "reason": "x"}
ctx.hook(module.DAYS_TARGET)(lambda self, d: self.elapse_days(d), app, 40)
book = ledger_of(app)
check("足した勢力の組でも情勢が進む", book["events"] and book["events"][-1]["a"] in ("f2", "f3")
      and book["relations"]["f2|f3"]["stage"] == -1, (book["events"], book["relations"]))
check("例外が無い（手で直した台帳）", not ctx.errors, ctx.errors)

print("[流れ] 勢力の刺客（会話のボタンと戦闘の後）")
module, ctx, app = fresh()
notes_fn()({"app": app, "npc_id": "30"})
store = module._store()["worlds"]
key = store.playthrough(app)
with store.lock:
    book = store.load(key)
    rules.hunters_of(book).update({
        "90": {"faction": "f1", "name": "ヴァルド", "title": "異端審問官", "town": "7", "status": "met", "day": 100},
        "91": {"faction": "f1", "name": "ゼイン", "title": "異端審問官", "town": "7", "status": "met", "day": 100}})
    before_score = book["standing"]["f1"]["score"]
    store.save(key)
for npc_id, name in (("90", "ヴァルド"), ("91", "ゼイン")):
    app.world.characters[npc_id] = Character(npc_id, name)
    app.world.characters[npc_id].config = {"is_dead": False}
    app.world.characters[npc_id].current_hp = 300
app.player.current_area = "7"
end_entry = {"text": "会話を終了する", "spec": PhaseSpec("ConversationEndManager", ["90", None, "<行動: 会話を終了する>"])}
app.buttons = [{"text": "売買する", "spec": PhaseSpec("ShoppingStartManagerRemake", [])}, end_entry]
hunter_provider = choices._store()["providers"][module.OWNER + ":hunter"]
picked = hunter_provider["claim"](app, choices.View(app, app.buttons))
check("刺客との会話は「受けて立つ」だけ（ゲームの終了のボタンの spec のまま文言だけ変えて出す）",
      [c.text for c in picked or []] == ["受けて立つ"] and picked[0].base is end_entry, picked)
buttons = list(app.buttons)
choices.compose(app, buttons)
check("窓口が組むと「受けて立つ」だけが並び、ゲームのボタンは書き換えない",
      [b["text"] for b in buttons] == ["受けて立つ"] and buttons[0]["spec"] is end_entry["spec"]
      and end_entry["text"] == "会話を終了する", buttons)
check("保存の前に外すと、ゲームの一覧（売買する・会話を終了する）に戻る",
      choices.strip(buttons) == ([app.buttons[0], end_entry], True), choices.strip(buttons))
other = [{"text": "会話を終了する", "spec": PhaseSpec("ConversationEndManager", ["30", None, "x"])}]
check("ほかの相手の会話は握らない", hunter_provider["claim"](app, choices.View(app, other)) is None)
ended = []
screen_cls = ui.Screen
original_end = screen_cls.end_conversation
screen_cls.end_conversation = lambda self, app_, entry, follow_up, **kw: ended.append((entry, kw.get("end_text")))
try:
    picked[0].on_press(app)
finally:
    screen_cls.end_conversation = original_end
check("「受けて立つ」を押すと、ゲームの終了のボタンで会話を閉じてから戦う", ended
      and ended[0][0] is end_entry and "ヴァルドの挑戦を受けて立ち" in ended[0][1], ended)
text = notes_fn()({"app": app, "npc_id": "90"}) or ""
check("刺客の会話に使命と勢力の構えを足す", "【刺客】あなたは白の教団の異端審問官" in text and "白の教団に属する者として" in text, text)


class Phase(object):
    def __init__(self, app, end_type=None):
        self.app, self.end_type = app, end_type


start_hook = ctx.hook("__main__:BattleStartManager.__init__")
end_hook = ctx.hook("__main__:BattleEndManager.end_phase")
start_hook(lambda self, *a, **k: None, Phase(app), app, "specific", ["90"])
check("刺客との戦闘を見分ける", ledger_of(app)["hunters"]["90"]["status"] == "fighting")
app.world.characters["90"].current_hp = 0
end_hook(lambda self: None, Phase(app, "won"))
book = ledger_of(app)
check("勝ったら刺客は死ぬ", book["hunters"]["90"]["status"] == "dead"
      and app.world.characters["90"].config["is_dead"] is True, book["hunters"]["90"])
check("勝ったら勢力の見られ方が下がり、来歴に刺客の名", book["standing"]["f1"]["score"] == max(-100, before_score - 10)
      and book["history"][-1]["kind"] == "hunter" and book["history"][-1]["incident"] == "白の教団の異端審問官",
      (book["standing"]["f1"], book["history"][-1:]))
check("勝ったら街の手配度が下がる", app.player.area_history["7"]["lawfulness"] == -170,
      app.player.area_history["7"])
check("勝ったら勢力に伝わることを1行出す", app.texts[-1:] == ["白の教団の異端審問官を討ったことは、白の教団の者たちにもすぐに伝わるだろう。"],
      app.texts)
start_hook(lambda self, *a, **k: None, Phase(app), app, "specific", ["91"])


def game_end_escaped(self):
    app.player.area_history["7"]["lawfulness"] -= 10      # ゲームは衛兵との戦闘として、逃げても 10 下げる


end_hook(game_end_escaped, Phase(app, "escaped"))
check("逃げたら刺客は生きていて、また現れる", ledger_of(app)["hunters"]["91"]["status"] == "escaped"
      and app.world.characters["91"].config["is_dead"] is False)
check("逃げたらゲームが下げた手配度を戦闘の前へ戻す", app.player.area_history["7"]["lawfulness"] == -170,
      app.player.area_history["7"])
start_hook(lambda self, *a, **k: None, Phase(app), app, "specific", ["91"])
start_hook(lambda self, *a, **k: None, Phase(app), app, "guard", None)
end_hook(lambda self: None, Phase(app, "won"))
check("刺客でない戦闘が後に始まれば前の印を持ち越さない", ledger_of(app)["hunters"]["91"]["status"] == "fighting")
check("例外が無い（刺客）", not ctx.errors, ctx.errors)

print("[世界を作るとき]")
WORLD_PROMPT = "面白そうな世界の概要を生成してください。\n【構造のルール】\n- 3層\n\n【注意】\n- 命名において…"
added = material.with_world_organizations(WORLD_PROMPT)
check("【注意】の前に【勢力】を足す", added is not None and added.index("【勢力】") < added.index("【注意】")
      and "2〜4つ" in added and added.startswith("面白そうな"), added)
check("二度は足さない", material.with_world_organizations(added) is None)
check("世界の生成でない頼みには足さない", material.with_world_organizations("会話を要約して下さい。") is None)
check("【注意】が無ければ末尾", material.with_world_organizations("【構造のルール】\n- x").endswith(
    material.WORLD_ORGANIZATIONS))

module, ctx, app = fresh(enable=False)
module.ORGANIZE_NEW_WORLDS = True
SENT_WORLD = []


def world_send(manager_name, message, structure=None, **kw):
    SENT_WORLD.append((manager_name, message))
    return {"ok": True}


send_hook = ctx.hook(module.SEND_TARGET)
send_hook(world_send, "create_world_overview",
          [{"role": "system", "content": "スキーマ"}, {"role": "user", "content": WORLD_PROMPT}], None)
check("世界の生成の頼みに足す", "【勢力】" in SENT_WORLD[-1][1][1]["content"]
      and SENT_WORLD[-1][1][0]["content"] == "スキーマ")
send_hook(world_send, "create_story", [{"role": "user", "content": WORLD_PROMPT}], None)
check("ほかの頼みには足さない", "【勢力】" not in SENT_WORLD[-1][1][0]["content"])
written = []
ctx.hook(module.WORLD_SAVE_TARGET)(lambda file_path, data: written.append(file_path),
                                   "C:/x/worlds/新しい世界/world_data.json",
                                   {"world_data": {"name": "新しい世界"}})
settings = getattr(sys, module.SETTINGS_STORE_ATTR)
check("作った世界の勢力を有効にする", written and settings.load("新しい世界", fresh=True).get("ENABLED") is True)
module.ORGANIZE_NEW_WORLDS = False
del SENT_WORLD[:]
send_hook(world_send, "create_world_overview", [{"role": "user", "content": WORLD_PROMPT}], None)
check("設定を切っていれば足さない", "【勢力】" not in SENT_WORLD[-1][1][0]["content"])

print("[流れ] 新しいキャラは出自から")
module, ctx, app = fresh()
app.player.area_history = {"0": {"lawfulness": 10, "achievements": [], "residency": {"total_days": 3}}}
for fn in ctx.ready:
    fn()
book = ledger_of(app)
check("出自の頼みを使う", ASKED[1][1][0]["content"] == material.STANDING_SYSTEM
      and "白の教団に故郷を追われた" in ASKED[1][1][1]["content"])
check("出自の判定の段をそのまま使う", book["standing"]["f1"]["score"] == -80
      and book["standing"]["f2"]["score"] == 40, book["standing"])

print("[流れ] 設定に勢力が無い世界に組織を設ける")
module, ctx, app = fresh()
ANSWERS[module.MANAGER_EXTRACT] = {"factions": [], "towns": [], "relations": [], "peaceful": "false"}
ANSWERS[module.MANAGER_DESIGN] = {"factions": [
    {"name": "灯守の会", "kind": "結社", "note": "灯を守る", "towns": ["0", "1"]},
    {"name": "潮騒商会", "kind": "商会", "note": "港を握る", "towns": ["1", "7", "99"]}],
    "relations": [{"a": "灯守の会", "b": "潮騒商会", "stage": -1}], "peaceful": "false"}
for fn in ctx.ready:
    fn()
check("切っていれば設けない", ledger_of(app)["status"] == rules.NONE
      and module.MANAGER_DESIGN not in [m for m, _ in ASKED])
module, ctx, app = fresh()
ANSWERS[module.MANAGER_EXTRACT] = {"factions": [], "towns": [], "relations": [], "peaceful": "false"}
ANSWERS[module.MANAGER_DESIGN] = {"factions": [
    {"name": "灯守の会", "kind": "結社", "note": "灯を守る", "towns": ["0", "1"]},
    {"name": "潮騒商会", "kind": "商会", "note": "港を握る", "towns": ["1", "7", "99"]}],
    "relations": [{"a": "灯守の会", "b": "潮騒商会", "stage": -1}], "peaceful": "false"}
ANSWERS[module.MANAGER_STANDING] = {"standings": []}
settings = getattr(sys, module.SETTINGS_STORE_ATTR)
from instantale_modloader.state import world_key  # noqa: E402
record = settings.load(world_key(app))
record["ADD_ORGANIZATIONS"] = True
settings.save(world_key(app))
for fn in ctx.ready:
    fn()
book = ledger_of(app)
check("抽出で勢力が無ければ設計を聞く", [m for m, _ in ASKED] == [
    module.MANAGER_EXTRACT, module.MANAGER_DESIGN, module.MANAGER_STANDING], [m for m, _ in ASKED])
check("設計した組織で台帳を作る（街は先の組織が勝つ・知らない街は捨てる）",
      book["status"] == rules.READY and [f["name"] for f in book["factions"]] == ["灯守の会", "潮騒商会"]
      and book["towns"] == {"0": {"faction": "f1", "evidence": ""}, "1": {"faction": "f1", "evidence": ""},
                            "7": {"faction": "f2", "evidence": ""}}, book["towns"])
check("設計した関係は動きにくい", book["relations"]["f1|f2"] == dict(
    book["relations"]["f1|f2"], stage=-1, from_setting=True))
check("答えが無い調整は土台のまま（f2 は手配 -160 で -80）",
      book["standing"]["f2"]["score"] == -80 and book["standing"]["f1"]["score"] == 10, book["standing"])

print("[規則] 最初の立ち位置")
records = rules.town_records({"0": {"achievements": ["a"] * 7, "residency": {"total_days": 120},
                                    "lawfulness": 10},
                              "1": {"achievements": [], "residency": {"total_days": 5}, "lawfulness": -5},
                              "2": {"achievements": [], "lawfulness": -150},
                              "3": {"achievements": ["a"] * 5, "residency": {"total_days": 229},
                                    "lawfulness": -170},
                              "4": {"achievements": ["a"] * 5, "residency": {"total_days": 100},
                                    "lawfulness": -20}})
check("街ごとの記録", records["0"]["days"] == 120 and len(records["0"]["deeds"]) == 7
      and records["2"]["days"] == 0 and records["1"]["law"] == -5)
check("新しいキャラの見分け", rules.is_new_character({"0": {"deeds": [], "days": 10, "law": 10}})
      and not rules.is_new_character({"0": {"deeds": [], "days": 120, "law": 10}})
      and not rules.is_new_character({"0": {"deeds": [], "days": 1, "law": -1}}))
toy = {"factions": [{"id": "f1", "name": "甲"}, {"id": "f2", "name": "乙"}, {"id": "f3", "name": "丙"},
                    {"id": "f4", "name": "丁"}, {"id": "f5", "name": "戊"}],
       "towns": {"0": {"faction": "f1"}, "1": {"faction": "f2"}, "2": {"faction": "f3"},
                 "3": {"faction": "f4"}, "4": {"faction": "f5"}}, "standing": {}}
base = rules.baseline_scores(toy, records)
by_name = {"霧送りの御堂": "f2", "灰の結社": "f1"}
check("名前の後ろの括弧書きを外して引く", rules.faction_id_of(by_name, "霧送りの御堂（祈祷教団）") == "f2"
      and rules.faction_id_of(by_name, "霧送りの御堂 (教団)") == "f2"
      and rules.faction_id_of(by_name, "「灰の結社」") == "f1"
      and rules.faction_id_of(by_name, "知らない勢力") is None)
check("土台の点数（功績は上限 50・顔なじみ +10／手配 -40・重い手配 -80）",
      base["f1"][0] == 60 and base["f2"][0] == -40 and base["f3"][0] == -80, base)
check("手配は功績で打ち消さない（重い手配は功績5件でも敵対・軽い手配は緊張の上端まで）",
      base["f4"][0] == -80 and base["f4"][1]["cap"] == -2
      and base["f5"][0] == -21 and base["f5"][1]["cap"] == -1 and base["f1"][1]["cap"] is None, base)
edge = rules.baseline_scores({"factions": [{"id": "f1"}, {"id": "f2"}],
                              "towns": {"0": {"faction": "f1"}, "1": {"faction": "f2"}}},
                             {"0": {"deeds": ["a"] * 5, "days": 100, "law": -40},
                              "1": {"deeds": ["a"] * 5, "days": 100, "law": -39}})
check("敵対の境目は手配度 -40", edge["f1"][0] == -80 and edge["f2"][0] == -21, edge)
rules.apply_adjusted_standing(toy, base, {"standings": [
    {"faction": "丁", "adjust": 1, "title": "x", "reason": "x"},
    {"faction": "戊", "adjust": 1, "title": "x", "reason": "x"},
    {"faction": "乙", "adjust": -1, "title": "仇", "reason": "x"},
    {"faction": "甲", "adjust": 1, "title": "恩人", "reason": "x"}]})
check("調整も手配の上限より上へ動かさない（下げるのと手配の無い勢力は動く）",
      [rules.standing_stage(toy["standing"][f]["score"]) for f in ("f4", "f5", "f2", "f1")]
      == [-2, -1, -2, 2] and toy["standing"]["f4"]["adjust"] == 0, toy["standing"])

print("[流れ] 勢力が1つの世界")
module, ctx, app = fresh()
ANSWERS[module.MANAGER_EXTRACT] = {"factions": EXTRACT_ANSWER["factions"][:1], "towns": [],
                                   "relations": [], "peaceful": "true"}
for fn in ctx.ready:
    fn()
check("作らない印を残す", ledger_of(app)["status"] == rules.NONE
      and [m for m, _ in ASKED] == [module.MANAGER_EXTRACT])
module._store()["failed"].clear()
for fn in ctx.ready:
    fn()
check("読み込み直しても聞き直さない", len(ASKED) == 1)
check("会話に足さない", notes_fn()({"app": app, "npc_id": "37"}) is None)

print("[流れ] 抽出が答えない")
module, ctx, app = fresh()
ANSWERS[module.MANAGER_EXTRACT] = None
for fn in ctx.ready:
    fn()
check("失敗の印", ledger_of(app)["status"] == rules.FAILED)
for fn in ctx.ready:
    fn()
check("同じ読み込みの間は聞き直さない", len(ASKED) == 1)
ANSWERS[module.MANAGER_EXTRACT] = EXTRACT_ANSWER
ctx.hook(module.LOAD_TARGETS[0])(lambda self: None, app)
check("読み込み直したらすぐもう一度試す", ledger_of(app)["status"] == rules.READY)
check("例外が無い", not ctx.errors, ctx.errors)

print("[流れ] 敵対が外れない印を会話で外す")
module, ctx, app = fresh()
for fn in ctx.ready:
    fn()
app.player.area_history["7"]["lawfulness"] = 10      # 手配の上限と分けるため、手配は解いておく
app.player.current_area = "7"
worlds = module._store()["worlds"]
key = worlds.playthrough(app)
with worlds.lock:
    book = worlds.load(key)
    book["standing"]["f1"].update(score=-70, locked=True)
    worlds.save(key)
del SENT[:]
close(ctx, Character("37", "巡礼町の司祭"), {"faction_change": 2, "faction_reconcile": "false"})
check("印のある相手には納得の判定を頼む", "faction_reconcile" in SENT[0][2].fields
      and "許しがたい敵" in SENT[0][1][0]["content"], SENT[0][1][0]["content"][-300:])
check("納得しなければ敵対の上端で止まる", ledger_of(app)["standing"]["f1"]["score"] == -61
      and ledger_of(app)["standing"]["f1"].get("locked"), ledger_of(app)["standing"])
app.world.days_elapsed += 1
close(ctx, Character("37", "巡礼町の司祭"), {"faction_change": 2, "faction_reconcile": "true"})
check("納得させたら印が外れ、上限なしで上がる", ledger_of(app)["standing"]["f1"]["score"] == -51
      and not ledger_of(app)["standing"]["f1"].get("locked"), ledger_of(app)["standing"])
del SENT[:]
app.player.current_area = "0"
close(ctx, Character("31", "東の村の番人"), {"faction_change": 0, "faction_reconcile": "true"})
check("印の無い相手には頼まない", "許しがたい敵" not in SENT[0][1][0]["content"])
check("例外が無い（印）", not ctx.errors, ctx.errors)

print("[規則] 関係の根拠は両方の勢力の名前を含む原文")
LOOSE = "白の教団は遺物を罪の証として排除する。\n灰の結社は古代文明を崇拝し、遺跡を守る。\n【街の一覧】"
loose_answer = {"factions": EXTRACTED["factions"][:2], "towns": [], "peaceful": "false",
                "relations": [{"a": "白の教団", "b": "灰の結社", "stage": -2, "evidence": "【街の一覧】"}]}
loose, dropped6 = rules.build_ledger(loose_answer, LOOSE, ["1"], day=1)
check("無関係な原文（材料の見出し）を根拠にした関係は捨てる", not loose["relations"]["f1|f2"]["from_setting"]
      and loose["relations"]["f1|f2"]["stage"] == 0
      and any("does not name both" in d for d in dropped6), (loose["relations"], dropped6))
one_name = dict(loose_answer, relations=[dict(loose_answer["relations"][0], evidence="白の教団は遺物を罪の証として排除する")])
check("片方の名前だけの根拠も捨てる", not rules.build_ledger(one_name, LOOSE, ["1"], day=1)[0]
      ["relations"]["f1|f2"]["from_setting"])
BOTH = LOOSE + "\n白の教団と灰の結社は長く争っている。"
rescued, _d = rules.build_ledger(loose_answer, BOTH, ["1"], day=1)
check("設定に2つの名前を1文で結ぶ文があれば、それを根拠にする",
      rescued["relations"]["f1|f2"]["from_setting"]
      and rescued["relations"]["f1|f2"]["evidence"] == "白の教団と灰の結社は長く争っている", rescued["relations"])

print("[規則] 暦が台帳より前に戻った")
book = rules.empty_ledger()
book.update(status=rules.READY, made_day=100, factions=[{"id": "f1", "name": "甲"}, {"id": "f2", "name": "乙"}],
            standing={"f1": {"score": -90, "locked": True}, "f2": {"score": 10}},
            relations={"f1|f2": {"stage": -1, "push": 0}}, gained={"f2": 250})
book["history"] = [{"day": 150, "faction": "f2", "before": 0, "after": 10, "kind": rules.TALK},
                   {"day": 250, "faction": "f1", "before": -70, "after": -90, "kind": rules.TALK}]
book["events"] = [{"day": 260, "a": "f1", "b": "f2", "direction": -1, "before": 0, "after": -1,
                   "rumor": "争いが始まったらしい"}]
book["trend"]["last_day"] = 260
check("戻っていなければ何もしない", rules.rewind(book, 300) is False)
check("戻ったら後で起きたことを外す", rules.rewind(book, 200) is True)
check("見られ方を来歴の前へ戻す（その動きで付いた敵対の印も外す）",
      book["standing"]["f1"] == {"score": -70} and book["standing"]["f2"]["score"] == 10, book["standing"])
check("勢力どうしの関係も戻し、噂も外す", book["relations"]["f1|f2"]["stage"] == 0 and book["events"] == [])
check("情勢の起点と1日1回の印も戻す", book["trend"]["last_day"] == 200 and book["gained"] == {}
      and [h["day"] for h in book["history"]] == [150], book)
check("台帳がその日より後に作られていれば作り直す", rules.rewind(dict(book, made_day=300), 200) == "rebuild")

print("[流れ] 勢力の無い世界に、後から組織を設ける設定を入れた")
module, ctx, app = fresh()
ANSWERS[module.MANAGER_EXTRACT] = {"factions": [], "towns": [], "relations": [], "peaceful": "false"}
for fn in ctx.ready:
    fn()
check("切っている間は勢力なし", ledger_of(app)["status"] == rules.NONE
      and not ledger_of(app)["organize_asked"])
ANSWERS[module.MANAGER_DESIGN] = None
settings = getattr(sys, module.SETTINGS_STORE_ATTR)
record = settings.load(world_key(app))
record["ADD_ORGANIZATIONS"] = True
settings.save(world_key(app))
del ASKED[:]
ctx.hook(module.LOAD_TARGETS[0])(lambda self: None, app)
check("設定を入れて読み込み直したら設計を聞く", [m for m, _ in ASKED] == [
    module.MANAGER_EXTRACT, module.MANAGER_DESIGN], [m for m, _ in ASKED])
check("設計が答えなければ「勢力なし」で固めず、失敗の印", ledger_of(app)["status"] == rules.FAILED)
ANSWERS[module.MANAGER_DESIGN] = {"factions": [
    {"name": "灯守の会", "kind": "結社", "note": "灯を守る", "towns": ["0"]},
    {"name": "潮騒商会", "kind": "商会", "note": "港を握る", "towns": ["7"]}],
    "relations": [], "peaceful": "false"}
ANSWERS[module.MANAGER_STANDING] = {"standings": []}
ctx.hook(module.LOAD_TARGETS[0])(lambda self: None, app)
check("読み込み直したら試し直して組織を設ける", ledger_of(app)["status"] == rules.READY
      and [f["name"] for f in ledger_of(app)["factions"]] == ["灯守の会", "潮騒商会"], ledger_of(app))
ANSWERS[module.MANAGER_DESIGN] = {"factions": [], "relations": [], "peaceful": "false"}
module, ctx, app = fresh()
ANSWERS[module.MANAGER_EXTRACT] = {"factions": [], "towns": [], "relations": [], "peaceful": "false"}
ANSWERS[module.MANAGER_DESIGN] = {"factions": [], "relations": [], "peaceful": "false"}
settings = getattr(sys, module.SETTINGS_STORE_ATTR)
record = settings.load(world_key(app))
record["ADD_ORGANIZATIONS"] = True
settings.save(world_key(app))
for fn in ctx.ready:
    fn()
del ASKED[:]
ctx.hook(module.LOAD_TARGETS[0])(lambda self: None, app)
check("設計を聞いて組織が2つに満たなければ、読み込み直しても聞き直さない",
      ledger_of(app)["status"] == rules.NONE and ledger_of(app)["organize_asked"] and not ASKED, ASKED)

print("[流れ] 台帳を作る前に終わった出来事は数えない")
module, ctx, app = fresh()
store = module._store()["worlds"]
key = store.playthrough(app)
incidents_dir = os.path.join(STATE_DIR, "town_incidents")
os.makedirs(incidents_dir, exist_ok=True)
with io.open(os.path.join(incidents_dir, key + ".json"), "w", encoding="utf-8") as fh:
    json.dump(INCIDENTS, fh, ensure_ascii=False)
for fn in ctx.ready:
    fn()
before = ledger_of(app)["standing"]["f2"]["score"]
check("作ったときに、終わっている出来事を数えた印に入れる",
      ledger_of(app)["trend"]["incidents"] == {"1": "player", "3": "natural"}, ledger_of(app)["trend"])
ctx.hook(module.DAYS_TARGET)(lambda self, d: self.elapse_days(d), app, 1)
check("最初の日数送りでまとめて上げない", ledger_of(app)["standing"]["f2"]["score"] == before
      and not [h for h in ledger_of(app)["history"] if h["kind"] == "incident"], ledger_of(app)["history"])
revived = json.loads(json.dumps(INCIDENTS))
revived["events"][0].update(status="active", ended=None, end_by=None)
with io.open(os.path.join(incidents_dir, key + ".json"), "w", encoding="utf-8") as fh:
    json.dump(revived, fh, ensure_ascii=False)
ctx.hook(module.DAYS_TARGET)(lambda self, d: self.elapse_days(d), app, 1)
check("古いセーブで進行中へ戻った出来事は、数えた印を外す（もう一度片付けたら数える）",
      "1" not in ledger_of(app)["trend"]["incidents"], ledger_of(app)["trend"]["incidents"])

print("[流れ] 答えを待つ間に別のセーブを読んだ")
module, ctx, app = fresh()


def switched(_message):
    app.save_data_dict["player_data"]["name"] = "別の主人公"
    return EXTRACT_ANSWER


ANSWERS[module.MANAGER_EXTRACT] = switched
for fn in ctx.ready:
    fn()
app.save_data_dict["player_data"]["name"] = "検査の主人公"
check("前の周回の台帳を、いまの app の主人公と日付で書かない", ledger_of(app)["status"] is None
      and module.MANAGER_STANDING not in [m for m, _ in ASKED], ledger_of(app)["status"])
ANSWERS[module.MANAGER_EXTRACT] = EXTRACT_ANSWER
ctx.hook(module.LOAD_TARGETS[0])(lambda self: None, app)
check("その周回を読み込み直したら作る", ledger_of(app)["status"] == rules.READY)

print("[流れ] 世界の生成が途中で止まった後に、別の世界を遊ぶ")
module, ctx, app = fresh(enable=False)
module.ORGANIZE_NEW_WORLDS = True
send_hook = ctx.hook(module.SEND_TARGET)
send_hook(world_send, "create_world_overview", [{"role": "user", "content": WORLD_PROMPT}], None)
existing = os.path.join(OUT_DIR, "worlds", "既存の世界", "world_data.json")
os.makedirs(os.path.dirname(existing), exist_ok=True)
with io.open(existing, "w", encoding="utf-8") as fh:
    fh.write("{}")
ctx.hook(module.WORLD_SAVE_TARGET)(lambda file_path, data: None, existing,
                                   {"world_data": {"name": "既存の世界"}})
settings = getattr(sys, module.SETTINGS_STORE_ATTR)
check("遊んでいる世界の保存では、その世界の勢力を有効にしない",
      settings.load("既存の世界", fresh=True).get("ENABLED") is not True)
module.ORGANIZE_NEW_WORLDS = False
send_hook(world_send, "create_world_overview", [{"role": "user", "content": WORLD_PROMPT}], None)
ctx.hook(module.WORLD_SAVE_TARGET)(lambda file_path, data: None, "C:/x/worlds/次の世界/world_data.json",
                                   {"world_data": {"name": "次の世界"}})
check("設定を切って作った次の新しい世界にも、前の印を持ち越さない",
      settings.load("次の世界", fresh=True).get("ENABLED") is not True)
check("例外が無い（後から足した場面）", not ctx.errors, ctx.errors)

print()
if failures:
    print("FAILED: {}".format(failures))
    sys.exit(1)
print("all ok")
