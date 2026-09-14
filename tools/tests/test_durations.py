# -*- coding: utf-8 -*-
"""`instantale_modloader.durations`（ゲームの期間の窓口）。

置く側と読む側が互いの名前を知らずに繋がること、
誰も置いていなければゲームの式が答えになること、
壊れた答えはゲームの式へ落ちることを見る。
日数送りの関所（`claim_days` / `resolve_days` / `install`）は、
望みが複数あるときの決め方と、包みが1世代に1枚であることを見る。
"""
import io
import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, os.pardir, os.pardir, "runtime"))

from instantale_modloader import durations  # noqa: E402


def app_aged(age):
    return types.SimpleNamespace(player=types.SimpleNamespace(age=age))


def check(label, cond, detail=""):
    print("  {} {}{}".format("ok  " if cond else "FAIL", label,
                             " -- {}".format(detail) if (detail and not cond) else ""))
    return bool(cond)


def main():
    ok = True
    logged = []
    write = logged.append
    if hasattr(sys, durations._ATTR):
        delattr(sys, durations._ATTR)

    print("誰も置いていない: ゲームの式")
    for age, months in ((None, 3), (20, 3), (29, 3), (30, 4), (31, 4), (40, 5),
                        (49, 5), (50, 6), (80, 6)):
        plan = durations.inn_stay(app_aged(age))
        ok &= check("{}歳 -> {}ヵ月".format(age, months),
                    plan["months"] == months and plan["days"] is None
                    and plan["source"] == "", plan)
    ok &= check("年齢が文字列でも読む", durations.inn_stay(app_aged("45"))["months"] == 5)
    ok &= check("日数は月数×30", durations.inn_stay_days(app_aged(31)) == 120)
    ok &= check("決めている MOD は無い", durations.source_of(durations.INN_STAY) == "")

    print("置く: 変える MOD の答えが勝つ")
    before = durations.declare(
        durations.INN_STAY,
        lambda app: {"months": 1, "days": 7, "length": "1週間"},
        owner="315_vacation_custom", write=write)
    ok &= check("前の持ち主は居なかった", before is None)
    plan = durations.inn_stay(app_aged(31))
    ok &= check("月数1・日数7", plan["months"] == 1 and plan["days"] == 7, plan)
    ok &= check("出どころが分かる", plan["source"] == "315_vacation_custom", plan)
    ok &= check("日数は縮んだほう", durations.inn_stay_days(app_aged(31)) == 7)

    print("置く側が None を返す: 「ゲームのまま」")
    durations.declare(durations.INN_STAY, lambda app: None, owner="315_vacation_custom")
    plan = durations.inn_stay(app_aged(31))
    ok &= check("ゲームの式へ落ちる", plan["months"] == 4 and plan["source"] == "", plan)

    print("壊れた答え: ゲームの式へ落ちて WARN")
    logged[:] = []
    durations.declare(durations.INN_STAY, lambda app: "1週間", owner="x")
    ok &= check("辞書でなければ落ちる", durations.inn_stay(app_aged(31), write=write)["months"] == 4)
    ok &= check("WARN が出る", any("not a dict" in line for line in logged), logged)
    logged[:] = []
    INN = durations.INN_STAY
    durations.declare(INN, lambda app: {"months": "many"}, owner="x")
    ok &= check("月数が読めなければ落ちる",
                durations.inn_stay(app_aged(31), write=write)["months"] == 4)
    ok &= check("WARN が出る", any("no usable months" in line for line in logged), logged)
    logged[:] = []

    def boom(app):
        raise RuntimeError("boom")

    durations.declare(INN, boom, owner="x")
    ok &= check("例外でも落ちる", durations.inn_stay(app_aged(31), write=write)["months"] == 4)
    ok &= check("例外は WARN に残る", any("raised RuntimeError" in line for line in logged),
                logged)

    print("置き換え: 後から置いたほうが勝ち、ログに残る")
    logged[:] = []
    before = durations.declare(INN, lambda app: {"months": 2}, owner="y", write=write)
    ok &= check("前の持ち主が返る", before == "x")
    ok &= check("置き換えがログに残る", any("now decided by 'y'" in line for line in logged),
                logged)
    ok &= check("月数だけでも均される",
                durations.inn_stay(app_aged(31)) == {"months": 2, "days": None,
                                                     "length": "2ヵ月", "source": "y"})

    print("外す: MOD を外したら置いたものも消える")
    gone = durations.forget("y", write=write)
    ok &= check("外した種類が返る", gone == [INN])
    ok &= check("ゲームの式に戻る", durations.inn_stay(app_aged(31))["source"] == "")
    try:
        durations.declare(INN, "no")
        ok &= check("関数でないものは置けない", False)
    except TypeError:
        ok &= check("関数でないものは置けない", True)

    print("注入し直しをまたぐ: 置き場は sys にある")
    durations.declare(INN, lambda app: {"months": 1, "days": 14}, owner="z")
    ok &= check("sys に残る", isinstance(getattr(sys, durations._ATTR, None), dict))
    durations.forget("z")

    print("街移動: 誰も置いていなければ実測の素の値")
    move = durations.area_move(app_aged(31))
    ok &= check("徒歩90・馬車14・運賃1000",
                move == {"walk_days": 90, "coach_days": 14, "coach_fare": 1000,
                         "source": ""}, move)

    print("街移動: 置いた関数に行き先が渡る")
    seen = []

    def by_distance(app, target_area_id=None):
        seen.append(target_area_id)
        far = str(target_area_id) == "9"
        return {"walk_days": 120 if far else 30, "coach_days": 21 if far else 7,
                "coach_fare": 1500 if far else 500}

    durations.declare(durations.AREA_MOVE, by_distance, owner="314_area_move_custom")
    near = durations.area_move(app_aged(31), target_area_id="2")
    far = durations.area_move(app_aged(31), target_area_id="9")
    ok &= check("行き先が関数へ届く", seen == ["2", "9"], seen)
    ok &= check("近い街の答え", (near["walk_days"], near["coach_days"], near["coach_fare"])
                == (30, 7, 500), near)
    ok &= check("遠い街の答え", (far["walk_days"], far["coach_days"], far["coach_fare"])
                == (120, 21, 1500), far)
    ok &= check("出どころが分かる", far["source"] == "314_area_move_custom")

    print("街移動: 欠けた項目は素の値で埋まる")
    durations.declare(durations.AREA_MOVE, lambda app, target_area_id=None:
                      {"walk_days": 45, "coach_days": "soon", "coach_fare": 0},
                      owner="x")
    move = durations.area_move(app_aged(31))
    ok &= check("読めない・0以下は素の値", move["coach_days"] == 14 and move["coach_fare"] == 1000
                and move["walk_days"] == 45, move)
    durations.forget("x")
    ok &= check("外せば素の値に戻る", durations.area_move(app_aged(31))["source"] == "")

    print("訓練: 誰も置いていなければ実測の素の値")
    plan = durations.training(app_aged(20))
    ok &= check("1年は365日", plan["days_per_year"] == 365, plan)
    ok &= check("開始時は3年", plan["course_years"] == 3, plan)
    ok &= check("活動の年数は 1/2/2/3",
                plan["activity_years"] == {"simple": 1, "fundamental": 2,
                                           "train_skill": 2, "learn_new_skill": 3}, plan)
    ok &= check("3年の活動は1095日（実測の elapse_days(1095)）",
                durations.training_days(app_aged(20), "learn_new_skill") == 1095)
    ok &= check("知らない種類は1年ぶん", durations.training_days(app_aged(20), "?") == 365)

    print("訓練: 置いた答えは一部でもよく、残りは素の値で埋まる")
    durations.declare(durations.TRAINING,
                      lambda app: {"days_per_year": 30, "activity_years": {"simple": 2}},
                      owner="x")
    plan = durations.training(app_aged(20))
    ok &= check("1年が30日に", plan["days_per_year"] == 30, plan)
    ok &= check("simple だけ2年、他は素の値",
                plan["activity_years"]["simple"] == 2
                and plan["activity_years"]["learn_new_skill"] == 3, plan)
    ok &= check("段の日数も追う", durations.training_days(app_aged(20), "simple") == 60)
    ok &= check("開始時の年数は置かなければ素の値", plan["course_years"] == 3, plan)
    durations.forget("x")
    ok &= check("外せば戻る", durations.training(app_aged(20))["source"] == "")

    print("日数送りの関所: 誰も望まなければ素通し")
    for attr in (durations._CLAIMS_ATTR, durations._GATE_ATTR):
        if hasattr(sys, attr):
            delattr(sys, attr)
    app = app_aged(31)
    ok &= check("望みが無ければそのまま", durations.resolve_days(app, 90) == 90)

    print("日数送りの関所: 1本だけなら、その値（増やす方向も通る）")
    logged[:] = []
    notes = []
    durations.claim_days("314_area_move_custom",
                         lambda app, days: {"days": 270, "since": 100.0},
                         note=lambda app, days, granted: notes.append(("314", days, granted)),
                         write=write)
    ok &= check("増やす方向も通る（距離補正）", durations.resolve_days(app, 90) == 270)
    ok &= check("note に実際の日数が来る", notes == [("314", 90, 270)], notes)
    ok &= check("決めた MOD がログに出る",
                any("decided 270" in line and "314_area_move_custom" in line
                    for line in logged), logged)

    print("日数送りの関所: 2本なら、先に始まった事情が決める")
    logged[:] = []
    notes[:] = []
    durations.claim_days("307_area_move_dungeon",
                         lambda app, days: {"days": 14, "since": 50.0},
                         note=lambda app, days, granted: notes.append(("307", days, granted)),
                         write=write)
    ok &= check("先に始まった 307 の 14 が通る", durations.resolve_days(app, 90) == 14)
    ok &= check("負けたほうにも実際の日数が来る",
                sorted(notes) == [("307", 90, 14), ("314", 90, 14)], notes)
    ok &= check("誰が決めて誰が望んだかログに出る",
                any("307_area_move_dungeon decided 14" in line
                    and "314_area_move_custom wanted 270" in line for line in logged),
                logged)

    print("日数送りの関所: 決める側でなくても、短くする側には回れる")
    logged[:] = []
    durations.claim_days("314_area_move_custom",
                         lambda app, days: {"days": 7, "since": 100.0}, write=write)
    ok &= check("頭打ちは通る（14 -> 7）", durations.resolve_days(app, 90) == 7)
    ok &= check("切り詰めた MOD がログに出る",
                any("capped to 7" in line for line in logged), logged)

    print("日数送りの関所: since を言わない望みは決める側にならない")
    durations.forget("307_area_move_dungeon")
    durations.forget("314_area_move_custom")
    durations.claim_days("a", lambda app, days: 5)            # since 無し
    durations.claim_days("b", lambda app, days: {"days": 40, "since": 1.0})
    ok &= check("since を言った b が決め、a が頭打ち", durations.resolve_days(app, 90) == 5)
    durations.forget("b")
    ok &= check("1本だけになれば、その値", durations.resolve_days(app, 90) == 5)
    durations.forget("a")

    print("日数送りの関所: 壊れた望みは無視して WARN")
    logged[:] = []

    def bad_wish(app, days):
        raise RuntimeError("boom")

    durations.claim_days("x", bad_wish, write=write)
    durations.claim_days("y", lambda app, days: {"days": 3, "since": 1.0}, write=write)
    ok &= check("壊れていないほうで決まる", durations.resolve_days(app, 90) == 3)
    ok &= check("例外は WARN に残る",
                any("raised RuntimeError" in line and "WARN" in line for line in logged),
                logged)
    logged[:] = []
    durations.claim_days("x", lambda app, days: {"days": "すぐ"}, write=write)
    ok &= check("数でない望みも無視", durations.resolve_days(app, 90) == 3)
    ok &= check("数でない望みは WARN",
                any("not a number" in line for line in logged), logged)
    durations.forget("x")
    durations.forget("y")

    print("日数送りの関所: 外した MOD の望みは残らない")
    durations.claim_days("z", lambda app, days: {"days": 1, "since": 1.0})
    ok &= check("外すと望みも消える", durations.forget("z") == ["days"]
                and durations.resolve_days(app, 90) == 90)

    print("日数送りの関所: 包むのは1世代に1枚")
    calls = []

    class FakeCtx(object):
        """`ctx.wrap` だけを持つ偽物。世代は本物と同じく apply() ごとに変わる。"""

        def __init__(self, generation):
            self.generation = generation
            self.wrapped = {}

        def wrap(self, target, required=False, safe=False):
            def deco(fn):
                calls.append(target)
                self.wrapped[target] = fn
                return fn
            return deco

    for attr in (durations._CLAIMS_ATTR, durations._GATE_ATTR):
        if hasattr(sys, attr):
            delattr(sys, attr)
    first = FakeCtx(1)
    targets = durations.install(first, write)
    second = FakeCtx(1)
    durations.install(second, write)
    ok &= check("同じ世代では2枚目を包まない", calls == [durations.DAYS_TARGET], calls)
    ok &= check("包んだ対象を返す", targets == [durations.DAYS_TARGET], targets)
    third = FakeCtx(2)
    durations.install(third, write)
    ok &= check("世代が変われば包み直す", len(calls) == 2, calls)

    print("日数送りの関所: 包みは orig を必ず呼ぶ")
    passed = []

    def orig(self, days, *args, **kwargs):
        passed.append(days)
        return "done"

    gate = third.wrapped[durations.DAYS_TARGET]
    durations.claim_days("q", lambda app, days: {"days": 5, "since": 1.0})
    ok &= check("関所が日数を差し替える", gate(orig, app, 90) == "done" and passed == [5],
                passed)
    passed[:] = []
    ok &= check("0 以下は誰にも聞かない", gate(orig, app, 0) == "done" and passed == [0],
                passed)
    passed[:] = []
    ok &= check("日数でないものはそのまま通す",
                gate(orig, app, None) == "done" and passed == [None], passed)
    durations.forget("q")
    passed[:] = []
    ok &= check("望みが無ければ渡された日数のまま",
                gate(orig, app, 90) == "done" and passed == [90], passed)

    print("OK" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
