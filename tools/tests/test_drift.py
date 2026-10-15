# -*- coding: utf-8 -*-
"""変化の見張り（`instantale_modloader.drift`）をゲーム抜きで通す。

    python tools/tests/test_drift.py

偽のモジュール（`fakedrift`）を `sys.modules` に差し込み、本物の `patch.wrap` で包んでから、
ゲームがする差し替えを真似て次を確かめる。

  外れ       … 差し替えられた包みを2回目の見回りで見つけ、付け直す（1回目は控えるだけ）
  包み直し   … ゲームが包みを `functools.wraps` や閉包で包み直しても、中に残っていれば外れと言わない
  上限       … 付け直しは対象ごとに `HEAL_LIMIT` 回まで。切ってあれば報告だけ
  引数       … 前の版と引数が変わった対象を、包んだ MOD と一緒に出す
  書き換え   … 前の版では当たっていた書き換えが、今の版で当たらなくなったら出す（見込みが少なければ出さない）
"""
import functools
import os
import shutil
import sys
import tempfile
import types

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(_ROOT, "runtime"))

import instantale_modloader  # noqa: E402,F401
from instantale_modloader import drift  # noqa: E402
from instantale_modloader import patch as P  # noqa: E402
from instantale_modloader import patch_registry as R  # noqa: E402

FAILURES = []


def check(label, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + label + ("" if cond else " :: {!r}".format(detail)))
    if not cond:
        FAILURES.append(label)


def fresh(generation="gen_a"):
    """見張りの状態と包みの控えを空にし、偽のモジュールを素に戻す。"""
    if hasattr(sys, drift._STATE_ATTR):
        delattr(sys, drift._STATE_ATTR)
    P.revert_all()
    P.set_generation(generation)
    module = types.ModuleType("fakedrift")
    module.send = lambda text: ("game", text)
    sys.modules["fakedrift"] = module
    return module


def hook(name="999_test"):
    R.begin_mod(name)
    try:
        @P.wrap("fakedrift:send")
        def send(orig, text):
            return ("mod",) + orig(text)
    finally:
        R.end_mod()


def test_lost_and_heal():
    print("包みの外れ（差し替えを見つけて付け直す）")
    game = fresh()
    hook()
    check("最初は包みが効いている", game.send("x") == ("mod", "game", "x"), game.send("x"))
    check("外れが無ければ何も出さない", drift.audit() == [])
    game.send = lambda text: ("game2", text)           # ゲームが差し替えた
    check("1回目の見回りでは控えるだけ", drift.audit() == [])
    found = drift.audit()
    check("2回目の見回りで外れを出す",
          len(found) == 1 and found[0]["target"] == "fakedrift:send"
          and found[0]["mods"] == ["999_test"], found)
    check("付け直して効いている", game.send("x") == ("mod", "game2", "x"), game.send("x"))
    check("付け直した後は出さない", drift.audit() == [] and drift.audit() == [])
    report = drift.report()["lost"]
    check("報告に回数と付け直しが残る",
          report and report[0]["times"] == 1 and report[0]["healed"] == 1, report)


def test_rewrapped_by_game():
    print("ゲームが包みを包み直しても、中に残っていれば外れと言わない")
    game = fresh()
    hook()
    ours = game.send

    @functools.wraps(ours)                             # 言語の包みの形（印が写る）
    def localized(text):
        return ("ru",) + ours(text)
    game.send = localized
    check("functools.wraps の包み直し", drift.audit() == [] and drift.audit() == [])

    def make(inner):
        def closure(text):                             # __wrapped__ を持たない包み
            return ("closure",) + inner(text)
        return closure
    game.send = make(ours)
    check("閉包の包み直し", drift.audit() == [] and drift.audit() == [])


def test_limit_and_off():
    print("付け直しの上限と、切ったとき")
    game = fresh()
    hook()
    for n in range(drift.HEAL_LIMIT):
        game.send = lambda text, n=n: ("game{}".format(n), text)
        drift.audit()
        drift.audit()
    game.send = lambda text: ("again", text)
    drift.audit()
    drift.audit()
    check("上限を超えたら付け直さない", game.send("x") == ("again", "x"), game.send("x"))
    report = drift.report()["lost"][0]
    check("回数と付け直しの数", report["times"] == drift.HEAL_LIMIT + 1
          and report["healed"] == drift.HEAL_LIMIT, report)

    game = fresh()
    hook()
    game.send = lambda text: ("plain", text)
    drift.audit(heal=False)
    found = drift.audit(heal=False)
    check("切ってあれば報告だけ", found and game.send("x") == ("plain", "x"), found)


def test_loader_watch_rearm():
    print("ローダ自身の見張りを包み直した後、見回りが二重に付け直さない")
    from instantale_modloader import llm
    game = fresh()
    lines = []
    ctx = types.SimpleNamespace(resolve=P.resolve, wrap=P.wrap, superseded=lambda: False,
                                log=lambda msg, level="INFO": lines.append(msg),
                                log_exc=lambda msg: lines.append("EXC " + msg))
    llm._WATCHES[:] = []

    def install(target):
        @ctx.wrap(target, required=False)
        def guard(orig, text):
            return ("guard",) + orig(text)

    R.begin_mod("(loader)")                            # boot がローダの部品を当てるときと同じ
    try:
        llm.watch_aliases(ctx, ["fakedrift:send"], install, label="loader watch")
    finally:
        R.end_mod()
    game.send = lambda text: ("rebuilt", text)         # ゲームが作り直した
    llm.rearm_watches(ctx)                             # 見張りのスレッドから（MOD の外）
    drift.audit()
    check("外れと言わない", drift.audit() == [], drift.report()["lost"])
    check("包みは1枚", game.send("x") == ("guard", "rebuilt", "x"), game.send("x"))
    llm._WATCHES[:] = []


def test_signatures(tmp):
    print("引数の変化・対象の消失（recon の changes.json を包んだ対象で絞る）")
    import datetime
    import json as _json
    from instantale_modloader import recon
    out_dir = os.path.join(tmp, "sig", "out")
    current = os.path.join(recon.snapshot_dir(out_dir), recon.CURRENT_DIR_NAME)
    os.makedirs(current)
    path = os.path.join(current, recon.CHANGES_NAME)

    def plant(detected):
        with open(path, "w", encoding="utf-8") as fh:
            _json.dump({"since": "main_025_20261014_2.zip", "detected_at": detected, "functions": [
                {"kind": "changed", "key": "fakedrift:send", "before": "(text)",
                 "now": "(text, area_difficulty=None)"},
                {"kind": "changed", "key": "fakedrift:other", "before": "(a)", "now": "(a, b)"},
                {"kind": "added", "key": "fakedrift:new", "before": None, "now": "(x)"}]}, fh)

    fresh()
    check("recon の比べが無ければ何も出さない", drift.check_signatures(out_dir) == [])
    plant(datetime.datetime.now().isoformat(timespec="seconds"))
    hook()
    found = drift.check_signatures(out_dir)
    check("包んだ対象の変化だけを、包んだ MOD と一緒に出す",
          [(item["target"], item["mods"]) for item in found] == [("fakedrift:send", ["999_test"])], found)
    check("同じ変化は1プロセスに1回", drift.check_signatures(out_dir) == [])
    check("報告にも載る", drift.report()["signatures"] == found)

    fresh("gen_b")
    plant("2000-01-01T00:00:00")
    hook()
    check("recon が版の変化を見つけてから {} 日を過ぎたら出さない".format(drift.REPORT_DAYS),
          drift.check_signatures(out_dir) == [])


def test_rewrites(tmp):
    print("書き換えの空振り")
    out_dir = os.path.join(tmp, "rw", "out")
    fresh()
    drift._state()["build"] = "main_001|100"
    for _ in range(6):
        drift.note_rewrite("prompt replace", "battle_referee", True)
        drift.note_rewrite("prompt replace", "battle_referee", False)
        drift.note_rewrite("rare fix", "court", False)
    drift.note_rewrite("rare fix", "court", True)
    for _ in range(3):
        drift.note_rewrite("rare fix", "court", True)
    check("前の版が無ければ何も出さない", drift.flush_rewrites(out_dir) == [])

    fresh("gen_b")
    drift._state()["build"] = "main_002|200"
    for _ in range(12):
        drift.note_rewrite("prompt replace", "battle_referee", False)
    for _ in range(3):
        drift.note_rewrite("rare fix", "court", False)
    found = drift.flush_rewrites(out_dir)
    check("当たらなくなった書き換えを出す",
          [(item["label"], item["manager"]) for item in found]
          == [("prompt replace", "battle_referee")], found)
    check("見込みが少ないうちは出さない（3回で見込み 3×4/10）",
          all(item["label"] != "rare fix" for item in found))
    for _ in range(12):
        drift.note_rewrite("prompt replace", "battle_referee", False)
    check("同じ空振りは1回だけ出す", drift.flush_rewrites(out_dir) == [])
    stored = drift._read(os.path.join(drift.store_dir(out_dir), drift.REWRITES_NAME))
    now = [entry for entry in stored["builds"] if entry["build"] == "main_002|200"][0]
    check("控えへ足すのは前回からの差分だけ", now["counts"]["prompt replace|battle_referee"] == [24, 0],
          now["counts"])


def main():
    tmp = tempfile.mkdtemp(prefix="instantale_drift_")
    try:
        test_lost_and_heal()
        test_rewrapped_by_game()
        test_limit_and_off()
        test_loader_watch_rearm()
        test_signatures(tmp)
        test_rewrites(tmp)
    finally:
        P.revert_all()
        sys.modules.pop("fakedrift", None)
        shutil.rmtree(tmp, ignore_errors=True)
    if FAILURES:
        print("\n失敗: " + ", ".join(FAILURES))
        return 1
    print("\nall good")
    return 0


if __name__ == "__main__":
    sys.exit(main())
