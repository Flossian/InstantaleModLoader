# -*- coding: utf-8 -*-
"""336_crime_overhaul の裁判への介入の決まり（trial.py）をゲーム抜きで通す。

    python tools/tests/test_crime_trial.py

  判決   … 判決の画面のボタンから死刑か懲役の年数を読む
  求刑   … 検察の求刑の年数を読む。死刑の求刑は None
  改め方 … 弁護人・司法取引・買収の成功で死刑を外し、求刑に掛けた年数を上限にする。
             判事がそれより軽ければ判事のまま。露見は年数を足す。何も無ければ変えない
  頼み   … 判事への一文、情状の段、壊れた人生の記録の置き換え
  捕まる前後 … 衛兵の買収の確率（encounter.py）、処刑場からの脱出の難易度と手引きする人（rescue.py）
  盗品   … よその店・盗品買取商での買い取り額（rules.stolen_sell_price）、盗品買取商の台詞の割合（fence.rate_label）
"""
import importlib
import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
RUNTIME_DIR = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir, "runtime"))
MODS_DIR = os.path.join(RUNTIME_DIR, "mods")

if RUNTIME_DIR not in sys.path:
    sys.path.insert(0, RUNTIME_DIR)


def load_parts():
    matches = sorted(name for name in os.listdir(MODS_DIR) if name.endswith("_crime_overhaul"))
    if len(matches) != 1:
        raise SystemExit("cannot find exactly one *_crime_overhaul: {}".format(matches))
    package = types.ModuleType("crime_overhaul_trial_parts")
    package.__path__ = [os.path.join(MODS_DIR, matches[0])]
    sys.modules[package.__name__] = package
    return importlib.import_module(package.__name__ + ".trial")


trial = load_parts()
encounter = importlib.import_module("crime_overhaul_trial_parts.encounter")
rescue = importlib.import_module("crime_overhaul_trial_parts.rescue")
rules = importlib.import_module("crime_overhaul_trial_parts.rules")
fence = importlib.import_module("crime_overhaul_trial_parts.fence")
cellmate = importlib.import_module("crime_overhaul_trial_parts.cellmate")
failures = []
CUTS = {"lawyer": 30, "plea": 50, "bribe": 70}


def check(name, cond, detail=""):
    print("  {}  {}{}".format("ok  " if cond else "FAIL", name,
                              " ({})".format(detail) if detail and not cond else ""))
    if not cond:
        failures.append(name)


def effects(lawyer=False, plea=False, bribe=None):
    return {"lawyer": lawyer, "plea": plea, "bribe": bribe}


def button(cls_name, args):
    return {"spec": {"cls_name": cls_name, "args": args}}


def spec_name(entry):
    return entry["spec"]["cls_name"]


def spec_args(entry):
    return entry["spec"]["args"]


def scene_verdict():
    print("判決")
    check("死刑", trial.verdict_of([button("ExecutionPhaseManager", [])], spec_name, spec_args)
          == ("death", None))
    check("懲役の年数", trial.verdict_of([button("ImprisonmentStartManager", [15, "罪", "事"])],
                                     spec_name, spec_args) == ("imprisonment", 15))
    check("判決でない画面は None", trial.verdict_of([button("TrialPhaseManager", [])],
                                              spec_name, spec_args) is None)
    check("求刑の年数", trial.requested_years(
        {"sentencing_request": {"type": "imprisonment", "years": 25}}) == 25)
    check("死刑の求刑は None", trial.requested_years(
        {"sentencing_request": {"type": "death_penalty"}}) is None)
    check("読めない求刑は None", trial.requested_years(None) is None)
    check("死刑の求刑を見分ける", trial.demanded_death(
        {"sentencing_request": {"type": "death_penalty"}}))
    check("懲役の求刑は死刑ではない", not trial.demanded_death(
        {"sentencing_request": {"type": "imprisonment", "years": 25}}))
    check("読めない求刑は死刑ではない", not trial.demanded_death(None))


def scene_adjust():
    print("改め方")
    adjust = trial.adjust
    check("介入が無ければ変えない", adjust("death", None, 15, effects(), CUTS, 20, 5)
          == ("death", None, []))
    kind, years, reasons = adjust("death", None, 15, effects(lawyer=True), CUTS, 20, 5)
    check("弁護人で死刑は懲役（求刑15の7割＝11年）", (kind, years) == ("imprisonment", 11)
          and reasons == [trial.REASON_LAWYER], (kind, years, reasons))
    kind, years, _ = adjust("death", None, None, effects(lawyer=True), CUTS, 20, 5)
    check("死刑の求刑なら基の年数から（20の7割＝14年）", (kind, years) == ("imprisonment", 14))
    # 死刑の求刑で判事が懲役30年: court が基の年数（20）を求刑として渡す
    kind, years, _ = adjust("imprisonment", 30, 20, effects(lawyer=True), CUTS, 20, 5)
    check("死刑の求刑で判事が懲役30年でも基の年数から（14年）", (kind, years) == ("imprisonment", 14), years)
    kind, years, _ = adjust("imprisonment", 25, 25, effects(plea=True), CUTS, 20, 5)
    check("司法取引で懲役25年は半分（13年）", (kind, years) == ("imprisonment", 13), years)
    check("判事が既に軽ければ判事のまま", adjust("imprisonment", 5, 25, effects(lawyer=True),
                                       CUTS, 20, 5)[:2] == ("imprisonment", 5))
    kind, years, _ = adjust("imprisonment", 25, 25, effects(lawyer=True, plea=True), CUTS, 20, 5)
    check("重ねると掛け合わせる（25×0.7×0.5＝9年）", years == 9, years)
    kind, years, reasons = adjust("imprisonment", 15, 15, effects(bribe="ok"), CUTS, 20, 5)
    check("買収の成功（15の3割＝5年）", years == 5 and reasons == [trial.REASON_BRIBE], years)
    kind, years, reasons = adjust("imprisonment", 15, 15, effects(bribe="caught"), CUTS, 20, 5)
    check("露見は年数を足す（20年）", years == 20 and reasons == [trial.REASON_CAUGHT], years)
    kind, years, reasons = adjust("death", None, 15, effects(bribe="caught"), CUTS, 20, 5)
    check("露見だけなら死刑は死刑のまま", (kind, years, reasons) == ("death", None, []))
    kind, years, _ = adjust("death", None, 15, effects(lawyer=True, bribe="caught"), CUTS, 20, 5)
    check("弁護人と露見なら、改めた懲役に足す（11＋5＝16年）", (kind, years) == ("imprisonment", 16))
    line = trial.verdict_line("imprisonment", 12, "imprisonment", 14,
                              [trial.REASON_LAWYER, trial.REASON_PLEA, trial.REASON_CAUGHT], 5)
    check("減らした分と贈賄の分を分けて知らせる",
          "懲役12年から懲役9年に改められた" in line and "刑は懲役14年に延びた" in line, line)
    check("減らしただけなら1文", trial.verdict_line("death", None, "imprisonment", 11,
                                               [trial.REASON_LAWYER], 5)
          == "（弁護人の異議が通り、判決は死刑から懲役11年に改められた）")
    check("贈賄だけなら延びた分", trial.verdict_line("imprisonment", 15, "imprisonment", 20,
                                                [trial.REASON_CAUGHT], 5)
          == "（贈賄の罪が加わり、刑は懲役15年から懲役20年に延びた）")
    check("1年より短くしない", adjust("imprisonment", 1, 1, effects(bribe="ok"),
                                  {"bribe": 100}, 20, 5)[1] == 1)


def scene_prompt():
    print("頼み")
    check("介入が無ければ一文は空", trial.judge_notes(effects()) == "")
    notes = trial.judge_notes(effects(lawyer=True, bribe="caught"))
    check("弁護人と露見の一文", trial.NOTE_LAWYER in notes and trial.NOTE_CAUGHT in notes)
    text = trial.circumstances(20, 35, ["霧を晴らした", "", "盗賊を退けた"])
    check("情状の段に重さと活躍", "この土地 20" in text and "合計 35" in text
          and "霧を晴らした / 盗賊を退けた" in text, text)
    broken = "- 人生ログの全体象: <scripts.characters.Character object at 0x000001F18487D9F0>\n"
    fixed, did = trial.fix_life_log(broken, "{'day': '旅の記録'}")
    check("壊れた人生の記録を置き換える", did and "旅の記録" in fixed and "object at" not in fixed,
          fixed)
    check("置き換える文が無ければ触らない", trial.fix_life_log(broken, "") == (broken, False))
    check("壊れていなければ触らない", trial.fix_life_log("- 人生ログの全体象: 旅\n", "x")[1] is False)
    check("袖の下の額", trial.fee(24216, 200) == 48432)
    check("魅力15で基準の確率", trial.bribe_chance(15, 40, 3) == 40)


class Person(object):
    def __init__(self, name, affinity=None, dead=False):
        self.name = name
        self.is_dead = dead
        self.relationship = {} if affinity is None else {"player": {"affinity": affinity}}


def scene_capture():
    print("== 捕まる前後（衛兵の買収・処刑場からの脱出）")
    check("買収: 魅力15・手配なしで基準の確率", encounter.chance(15, 50, 3, 0, 0.5) == 50)
    check("買収: 手配の重さで下がる", encounter.chance(15, 50, 3, 20, 0.5) == 40)
    check("買収: 上限 95%", encounter.chance(30, 50, 3, 0, 0.5) == 95)
    check("買収: 下限 5%", encounter.chance(5, 50, 3, 200, 0.5) == 5)
    check("買収: 重さが負でも率を上げない", encounter.chance(15, 50, 3, -10, 0.5) == 50)

    class Spec(object):
        def __init__(self, cls_name, args=()):
            self.cls_name, self.args = cls_name, list(args)

    surrender = {"text": "大人しく捕まる", "spec": Spec("TrialStartManager", ["罪状", "出来事"])}
    resist = {"text": "抵抗する！", "spec": Spec("BattleStartManager", ["guard", None])}
    bribe = {"text": "金を握らせる（1G・成功率 50%）", "spec": Spec("JustSetButtonToNormalPhase"),
             "mod_crime_incentive_action": "encounter:bribe"}
    after = encounter.refusal_buttons([surrender, resist, bribe], resist)
    check("突き返された後: 大人しく捕まる・抵抗する！の2つだけ",
          [e["text"] for e in after or []] == ["大人しく捕まる", "抵抗する！"], after)
    check("突き返された後: 捕まるは写し（ゲームのボタンに印を書き込まない）",
          after is not None and after[0] is not surrender and after[0]["spec"] is surrender["spec"])
    check("突き返された後: 抵抗する！はゲームのボタンそのまま", after is not None and after[1] is resist)
    check("突き返された後: 捕まるが無ければ組まない", encounter.refusal_buttons([resist, bribe], resist) is None)
    check("突き返された後: 抵抗する！が無ければ組まない", encounter.refusal_buttons([surrender], None) is None)
    check("脱出: 土地とレベルの高いほう ＋ 上乗せ", rescue.rescue_difficulty(76, 80, 10) == 90)
    check("脱出: 土地の難易度が高ければそちら", rescue.rescue_difficulty(76, 40, 10) == 86)
    check("脱出: 片方だけ読めればそれを使う", rescue.rescue_difficulty(None, 30, 10) == 40)
    check("脱出: どちらも読めなければ None", rescue.rescue_difficulty(None, None, 10) is None)
    check("好感度を読む", rescue.affinity_of(Person("a", 80)) == 80)
    check("真偽値は好感度に数えない", rescue.affinity_of(Person("a", True)) is None)
    check("関係が無ければ None", rescue.affinity_of(Person("a")) is None)
    world = types.SimpleNamespace(characters={
        "player": Person("主人公", 100),
        "1": Person("低い", 40),
        "2": Person("高い", 75),
        "3": Person("もっと高いが死んだ", 99, dead=True),
        "4": Person("届く", 60),
    })
    app = types.SimpleNamespace(world=world)
    check("手引きはいちばん好感度の高い生きた人", rescue.rescuer_of(app, 60) == ("2", "高い", 75),
          rescue.rescuer_of(app, 60))
    check("線に届く人がいなければ None", rescue.rescuer_of(app, 80) is None)


def scene_stolen():
    print("== 盗品の値段（theft / fence）")
    check("よその店は半分", rules.stolen_sell_price(rules.SELL, 2553.3, 50) == 1276.65)
    check("盗品買取商は7割", abs(rules.stolen_sell_price(rules.SELL, 2553.3, 70) - 1787.31) < 0.001)
    check("買価には掛けない", rules.stolen_sell_price(rules.BUY, 2553.3, 50) is None)
    check("100% なら触らない", rules.stolen_sell_price(rules.SELL, 2553.3, 100) is None)
    check("額が無ければ触らない", rules.stolen_sell_price(rules.SELL, None, 50) is None)
    check("台詞の割合: 7割", fence.rate_label(70) == "7割")
    check("台詞の割合: 半端は %", fence.rate_label(65) == "65%")


def scene_cellmate():
    print("== 同房の囚人（cellmate）")
    check("1年で好感度が上がる", cellmate.raised_affinity(35, 10) == 45)
    check("読めない好感度は 0 から", cellmate.raised_affinity(None, 10) == 10)
    check("真偽値は好感度に数えない", cellmate.raised_affinity(True, 10) == 10)
    check("上がり幅が負でも下げない", cellmate.raised_affinity(35, -5) == 35)
    check("名前からファイルに使えない字を落とす", cellmate.clean_name("鉄/拳:のグレン") == "鉄拳のグレン")
    check("見た目の種類を語彙に均す", cellmate.clean_category("A Young Woman") == "young woman")
    check("見た目の句の先頭は種類", cellmate.clean_look("young woman, red hair, scar", "young woman")
          == ["young woman", "red hair", "scar"])


def scene_acquittal():
    print("無罪")
    judge = "【裁判】\n'主人公'の釈明は'あの夜は宿に居た。宿の主人が証人だ'です。\n判決を下してください。"
    check("判事の頼みから釈明を取り出す", trial.plea_of(judge) == "あの夜は宿に居た。宿の主人が証人だ",
          trial.plea_of(judge))
    check("釈明が無ければ None", trial.plea_of("判決を下してください") is None and trial.plea_of(None) is None)
    prison = {"sentencing_request": {"type": "imprisonment", "years": 15}}
    death = {"sentencing_request": {"type": trial.DEATH_PENALTY_TYPE}}
    plea = "あの夜は宿に居た。宿の主人が証人だ"
    check("書いた釈明があり懲役の求刑なら開く", trial.acquittal_open(plea, prison, effects(), 10)[0])
    check("素のボタンの文言だけでは開かない",
          not trial.acquittal_open("濡れ衣だ", prison, effects(), 0)[0]
          and not trial.acquittal_open("わかりました", prison, effects(), 0)[0])
    check("字数が足りなければ開かない", not trial.acquittal_open("無実だ", prison, effects(), 10)[0])
    check("死刑の求刑では開かない", not trial.acquittal_open(plea, death, effects(), 10)[0])
    check("贈賄が露見した回は開かない", not trial.acquittal_open(plea, prison, effects(bribe="caught"), 10)[0])
    check("弁護人・司法取引・受け取られた袖の下は無罪の道に関わらない",
          trial.acquittal_open(plea, prison, effects(lawyer=True, plea=True, bribe="ok"), 10)[0])
    check("懲役0年が無罪", trial.acquitted("imprisonment", 0)
          and not trial.acquitted("imprisonment", 1) and not trial.acquitted("death", None))
    check("懲役0年の判決の画面を読める", trial.verdict_of(
        [button(trial.PRISON_SPEC, [0, "罪", "出来事"])], spec_name, spec_args) == ("imprisonment", 0))
    check("判事への一文は弁護人を無罪の根拠にしない", "弁護人" in trial.NOTE_ACQUIT and "根拠にならない" in trial.NOTE_ACQUIT)


def main():
    scene_verdict()
    scene_adjust()
    scene_acquittal()
    scene_prompt()
    scene_capture()
    scene_stolen()
    scene_cellmate()
    print()
    if failures:
        print("FAILED: {}".format(failures))
        return 1
    print("all ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
