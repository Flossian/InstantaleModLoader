# -*- coding: utf-8 -*-
"""ローダの `_keep_save_gates`（保存の関所を今の世代で立て直す）のオフライン検証。

    前の世代で立っていて今の世代で誰も立てなかった関所だけを立て直す
    一度も立っていない関所・今の世代で立った関所には触らない
    1つの部品で落ちても残りは立て直す
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
RUNTIME = os.path.join(HERE, os.pardir, os.pardir, "runtime")
sys.path.insert(0, os.path.abspath(RUNTIME))

import instantale_modloader as ml  # noqa: E402
from instantale_modloader import modfacility, modnpc, prices  # noqa: E402

failures = []


def check(name, cond, detail=""):
    print("  {}  {}{}".format("ok  " if cond else "FAIL", name,
                              "" if cond else " -- {!r}".format(detail)))
    if not cond:
        failures.append(name)


calls = []
saved = (modnpc.installed, modnpc.install, modfacility.installed, modfacility.install,
         prices.item_gate, prices.install, ml.log_exc)


def fake(name, record, fail=False):
    def installed():
        return record

    def install(ctx, write=None):
        calls.append(name)
        if fail:
            raise RuntimeError("boom")
        return []
    return installed, install


try:
    ml.log_exc = lambda *a, **k: None
    ml._state["generation"] = "gen_now"
    modnpc.installed, modnpc.install = fake("modnpc", {"generation": "gen_old"})
    modfacility.installed, modfacility.install = fake("modfacility", None)
    prices.item_gate, prices.install = fake("prices", {"generation": "gen_now"})
    rebuilt = ml._keep_save_gates(object())
    check("前の世代の関所だけ立て直す", calls == ["modnpc"] and rebuilt == ["modnpc"], (calls, rebuilt))

    calls[:] = []
    modnpc.installed, modnpc.install = fake("modnpc", {"generation": "gen_old"}, fail=True)
    modfacility.installed, modfacility.install = fake("modfacility", {"generation": "gen_old"})
    rebuilt = ml._keep_save_gates(object())
    check("1つ落ちても残りは立て直す", calls == ["modnpc", "modfacility"] and rebuilt == ["modfacility"],
          (calls, rebuilt))
finally:
    (modnpc.installed, modnpc.install, modfacility.installed, modfacility.install,
     prices.item_gate, prices.install, ml.log_exc) = saved

print()
if failures:
    print("{} 件失敗".format(len(failures)))
    sys.exit(1)
print("all ok")
