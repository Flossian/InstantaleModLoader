# -*- coding: utf-8 -*-
"""925_labor_overhaul の設定画面（`tool.py`）を、ウィンドウを開かずに通す。

    python tools/tests/test_wip_labor_overhaul_tool.py

  置き方 … 宣言の設定がどれもちょうど1回、どこかのカードに置かれている
  試算   … 右の試算が、ゲームの中と同じ式（`rules.py`）で出る。設定を変えると変わる。打ちかけの欄では落ちない
  表記   … 通貨の表記と、欄の説明の1行
  宣言   … mod.json に tool が宣言され、ウィンドウを組む関数が在る（開くのは check_tool_screens）
"""
import importlib.util
import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir))
MODS_DIR = os.path.join(ROOT, "runtime", "mods")
sys.path.insert(0, os.path.join(ROOT, "tools"))

import modtool  # noqa: E402

matches = sorted(name for name in os.listdir(MODS_DIR) if name.endswith("_labor_overhaul"))
if len(matches) != 1:
    raise SystemExit("cannot pick *_labor_overhaul: {}".format(matches))
MOD_DIR = os.path.join(MODS_DIR, matches[0])

failures = []


def check(label, ok, detail=""):
    print(("  ok   " if ok else "  FAIL ") + label + ((" -- " + str(detail)) if detail and not ok else ""))
    if not ok:
        failures.append(label)


spec = importlib.util.spec_from_file_location("labor_overhaul_tool", os.path.join(MOD_DIR, "tool.py"))
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)

found = modtool.decls(MOD_DIR, ROOT)
rules = tool.load_rules(MOD_DIR)
defaults = dict((key, decl["default"]) for key, decl in found.items())

print("[置き方]")
placed = tool.placed_keys()
check("同じ設定を2か所に置いていない", len(placed) == len(set(placed)),
      sorted(key for key in set(placed) if placed.count(key) > 1))
check("置いた名前はどれも宣言に在る", all(key in found for key in placed),
      [key for key in placed if key not in found])
check("宣言の設定はどれも置かれている", sorted(placed) == sorted(found),
      sorted(set(found) - set(placed)))
check("入切は宣言に在る真偽", all(card[2] is None or found[card[2]]["type"] == "bool" for card in tool.CARDS))
by_key = dict((card[0], card) for card in tool.CARDS)
check("1回の長さ・一覧の件数・入れ替えは役場の契約のカード",
      all(key in tool.card_keys(by_key["contract"]) for key in ("TERM_DAYS", "LIST_JOBS", "ROTATE_DAYS")))
check("勤続の段階は3列ぶんの欄が3行",
      [len([c for c in cells if not c.startswith(tool.FIXED)])
       for part in by_key["seniority"][4] if part[0] == "table" for _label, cells in part[2]] == [3, 3, 3])
check("文の中の文字は設定名と取り違えない",
      not any(tool.is_setting(token, found) for token in ("日。", "%", "「働く」1回は", "中間")))
check("ひな形が変えるのは期間の3つだけ",
      all(sorted(values) == ["LONG_TERMS", "SHORT_TERMS", "TERM_DAYS"] for _label, values in tool.PRESETS))
check("ひな形の1つめは素のゲームの値（宣言の既定）",
      all(found[key]["default"] == value for key, value in tool.PRESETS[0][1].items()))

print("[試算]")
sample = 30                              # 難易度 30。素の賃金 178（120日ぶん）、依頼1件 4,234
check("測った表は難易度 0〜76", len(tool.LABOR_CURVE) == 77 and len(tool.QUEST_CURVE) == 77
      and tool.LABOR_CURVE[30] == 178 and tool.QUEST_CURVE[30] == 4234 and tool.LABOR_CURVE[76] == 449)
plain = tool.simulate(rules, found, defaults, sample, 11, 0)
check("能力値 11・勤続なしなら素の賃金", plain["pay"] == 178 and plain["base"] == 178 and plain["rank"] == "見習い", plain)
check("契約の稼ぎは割増こみ（4回 12.5%・8回 25%）",
      plain["short_total"] == 178 * 4 + int(178 * 4 * 0.125) and plain["long_total"] == 178 * 8 + int(178 * 8 * 0.25),
      (plain["short_total"], plain["long_total"]))
check("素の労働は割増なし", plain["plain_long"] == 178 * 8)
check("依頼1件との比べ", abs(plain["share"] - 100.0 * plain["long_total"] / 4234) < 1e-9)
strong = tool.simulate(rules, found, defaults, sample, 30, 2880)
check("能力値 30・右腕で 4 倍", strong["pay"] == 178 * 4 and strong["rank"] == "右腕" and strong["discount"] == 15, strong)
check("危険度ごとの賃金は 2・3・4 倍（90日ぶん）",
      [row["pay"] for row in plain["illegal"]] == [rules.term_pay(178, 90, (p,)) for p in (200, 300, 400)])
check("危険の確率は表の値（能力値 11）", [row["chance"] for row in plain["illegal"]] == [10.0, 18.0, 28.0],
      plain["illegal"])
check("能力値と勤続が高いと危険が下がる", strong["illegal"][0]["chance"] < plain["illegal"][0]["chance"])
monthly = tool.simulate(rules, found, dict(defaults, TERM_DAYS="30", SHORT_TERMS="3", LONG_TERMS="6"), sample, 11, 0)
check("1回の長さを変えると賃金と契約の長さが変わる",
      monthly["pay"] == rules.term_pay(178, 30) and monthly["short_days"] == 90 and monthly["long_days"] == 180,
      monthly)
check("1回が短いときの臨時の募集は、その長さと短い契約の回数",
      monthly["relief_days"] == 30 and monthly["relief_terms"] == 3 and plain["relief_days"] == 30
      and plain["relief_terms"] == 3)
tuned = tool.simulate(rules, found, dict(defaults, RANK2_DAYS="100", RANK2_PAY_PERCENT="300",
                                         LONG_BONUS_PERCENT="100"), sample, 11, 100)
check("画面の数を変えると試算が変わる", tuned["pay"] == 178 * 3 and tuned["long_total"] == 178 * 3 * 8 * 2, tuned)
off = tool.simulate(rules, found, dict(defaults, SENIORITY=False), sample, 11, 2880)
check("勤続を切ると勤続が効かない", off["pay"] == 178 and off["seniority"] is False)
check("値引きを切ると値引きが出ない",
      tool.simulate(rules, found, dict(defaults, SHOP_DISCOUNT=False), sample, 11, 2880)["discount"] == 0)
typing = tool.simulate(rules, found, dict(defaults, TERM_DAYS="", RANK3_DAYS="abc", RISK_PERCENT_1="-"), sample, 11, 0)
check("打ちかけ・空の欄は既定で試算する（落ちない）", typing["pay"] == 178 and typing["illegal"][0]["chance"] == 10.0)
check("試算の後に既定で呼ぶと既定の結果に戻る", tool.simulate(rules, found, defaults, sample, 11, 0) == plain)
grown = tool.simulate(rules, found, defaults, 0, 11, 0, 30)
check("街の実績は賃金の基準の難易度に足される（難易度 0 + 実績 30 → 178）",
      grown["pay"] == 178 and grown["level"] == 30 and grown["growth"] == 30, grown)
check("比べる素の労働と依頼は、主人公の最高難易度のまま", grown["plain_long"] == 6 * 8 and grown["quest"] == 7, grown)
check("街の実績は設定の上限で止まる", tool.simulate(rules, found, defaults, 0, 11, 0, 99)["growth"] == 60)
check("基準は 76 で止まる", tool.simulate(rules, found, defaults, 70, 11, 0, 60)["level"] == 76)
old = tool.simulate(rules, found, defaults, 30, 11, 0, 0, 50)
check("年齢 50 では1回が 240 日、賃金は変わらない", old["term_days"] == 240 and old["pay"] == 178, old)
check("年齢 55 で役場、50 で裏の事務所が断る",
      tool.simulate(rules, found, defaults, 30, 11, 0, 0, 55)["office_refused"] is True
      and old["office_refused"] is False and old["illegal_refused"] is True)
check("年齢の決まりを切ると伸びない",
      tool.simulate(rules, found, dict(defaults, AGE_RULES=False), 30, 11, 0, 0, 50)["term_days"] == 90)
check("年齢を渡さなければ伸ばさない", plain["term_days"] == 90 and plain["office_refused"] is False)
check("街の実績を切ると乗らない",
      tool.simulate(rules, found, dict(defaults, TOWN_GROWTH=False), 0, 11, 0, 30)["pay"] == 6)
check("闇労働の賃金にも街の実績が乗る", grown["illegal"][0]["pay"] == rules.term_pay(178, 90, (200,)))

print("[表記]")
check("通貨の表記は文字列（通貨の表記の MOD の一括設定か G）", isinstance(tool.currency(ROOT, MOD_DIR), str)
      and tool.currency(ROOT, MOD_DIR) != "")
check("通貨の表記の MOD が無ければ G", tool.currency(ROOT, os.path.join(HERE, "no_such_mods", "x")) == "G")
hint = tool.hint_text(found, "TERM_DAYS")
check("欄の説明に名前・説明・既定が出る", found["TERM_DAYS"]["label"]["ja"] in hint and "既定: 90" in hint, hint)
check("説明の無い項目は名前と既定だけ", tool.hint_text(found, "LONG_AFFINITY").count("／") == 1,
      tool.hint_text(found, "LONG_AFFINITY"))
check("金額は3桁区切り、確率は要らない 0 を出さない",
      tool.money(4234.4) == "4,234" and tool.percent_text(10.0) == "10" and tool.percent_text(7.25) == "7.2")

print("[宣言]")
with io.open(os.path.join(MOD_DIR, "mod.json"), encoding="utf-8") as fh:
    manifest = json.load(fh)
declared = manifest.get("tool") or {}
check("mod.json に tool が宣言され、入口が在る",
      declared.get("entry") == "tool.py" and os.path.isfile(os.path.join(MOD_DIR, "tool.py")), declared)
check("ウィンドウを組む関数が在る（開くのは check_tool_screens）", callable(getattr(tool, "build_window", None)))

print("\n失敗 {} 件".format(len(failures)))
if failures:
    raise SystemExit(1)
print("すべて通った")
