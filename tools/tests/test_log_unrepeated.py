# -*- coding: utf-8 -*-
"""ローダの定型の行（`log_unrepeated`）をゲーム抜きで通す。

    python tools/tests/test_log_unrepeated.py

1回の注入で、遅れて当て直す boot が何度か走る。
そのたびに `wrapped` / `replacing` などが同じ文面で並び直していたので、
前の boot で書いた行は書かないようにした。

  初回     … 最初の boot では全部書く。同じ boot の中の重複も書く（どの MOD の包みかを読むため）
  繰り返し … 次の boot では前の boot と同じ行を書かず、書かなかった数を数える
  新しい行 … 前の boot に無かった行は書く
  WARN     … `log` で書く行は毎回書く（ここを通さない）
  読み直し … 手で注入し直すとローダごと読み直されるので、控えは空から始まる
  置き換え … 同じ注入の前の boot の層を置き換えたときは数えるだけ。前の注入の層は対象ごとに書く
  先送り   … 締めの報告の節は待つモジュールごとの件数と MOD（対象ごとの行は `defer` の時点で出ている）
  MOD の行 … `ctx.log` の INFO 行も前の boot と同じなら書かない。WARN は毎回書く
  建物と NPC … `modfacility` / `modnpc` の `registered` は同じ持ち主・世代・層の数なら1行。snapshot と関所の行は中身が変わったときだけ
"""
import os
import sys
import tempfile

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(_ROOT, "runtime"))

import instantale_modloader as ml                      # noqa: E402

failures = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name
          + ((" -- " + str(detail)) if detail and not cond else ""))
    if not cond:
        failures.append(name)


def lines(path):
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as fh:
        return [line.rstrip("\n").split("] ", 1)[1].strip() for line in fh]


with tempfile.TemporaryDirectory() as out:
    path = os.path.join(out, "modloader.log")
    saved = {key: ml._state.get(key) for key in
             ("log_path", "logged_before", "logged_now", "repeats", "lines_written")}
    try:
        ml._state["log_path"] = path
        ml._state["logged_before"] = set()
        ml._state["logged_now"] = set()
        ml._state["repeats"] = 0

        # boot 1
        ml._roll_logged_lines()
        ml.log_unrepeated("wrapped a:f ('f')")
        ml.log_unrepeated("wrapped a:f ('f')")          # 2本の MOD が同じ対象を包んだ
        ml.log_unrepeated("applied: 100_x")
        first = lines(path)
        check("最初の boot は全部書く", len(first) == 3, first)
        check("同じ boot の中の重複は書く",
              first.count("INFO  wrapped a:f ('f')") == 2, first)

        # boot 2（遅れて当て直す）
        ml._roll_logged_lines()
        wrote = ml.log_unrepeated("wrapped a:f ('f')")
        ml.log_unrepeated("wrapped b:g ('g')")
        ml.log("WARN の行", level="WARN")
        ml.log("WARN の行", level="WARN")
        second = lines(path)[3:]
        check("前の boot と同じ行は書かない", not wrote and
              "INFO  wrapped a:f ('f')" not in second, second)
        check("新しい行は書く", "INFO  wrapped b:g ('g')" in second, second)
        check("書かなかった数を数える", ml._state["repeats"] == 1, ml._state["repeats"])
        check("log の行は毎回書く", second.count("WARN  WARN の行") == 2, second)

        # boot 3: boot 2 で初めて出た行も繰り返しになる
        ml._roll_logged_lines()
        check("boot の頭で数えを0に戻す", ml._state["repeats"] == 0, ml._state["repeats"])
        ml.log_unrepeated("wrapped b:g ('g')")
        ml.log_unrepeated("wrapped a:f ('f')")
        check("前のどの boot で出た行も書かない", len(lines(path)) == len(first) + len(second),
              lines(path))
        check("boot 3 の繰り返しの数", ml._state["repeats"] == 2, ml._state["repeats"])

        # 手で注入し直すと `_state` ごと作り直される（ローダが読み直される）
        ml._state["logged_before"] = set()
        ml._state["logged_now"] = set()
        ml._roll_logged_lines()
        check("読み直した後は最初からまた書く", ml.log_unrepeated("wrapped a:f ('f')"))
        # 置き換えた層の見分け。同じ注入の前の boot の層は数えるだけ、前の注入の層は対象ごとに書く。
        ml._state["generations"] = ["gen1", "gen2"]
        ml._state["replaced_own"] = 0
        check("同じ注入の前の boot の層は数える", ml.note_replaced("gen1") is True
              and ml._state["replaced_own"] == 1, ml._state["replaced_own"])
        check("前の注入の層は数えない（対象ごとに書く）", ml.note_replaced("old") is False)
        check("世代の無い層は数えない", ml.note_replaced(None) is False)
        check("今の boot の世代は前の boot に数えない", ml.note_replaced("gen2") is False)
        ml._roll_logged_lines()
        check("boot の頭で置き換えの数も0に戻す", ml._state["replaced_own"] == 0)
    finally:
        for key, value in saved.items():
            ml._state[key] = value
        ml._state["generations"] = []
        ml._state["replaced_own"] = 0

# 締めの報告の先送りの節は、待つモジュールごとの件数と MOD にまとめる。
# 対象ごとの行は、先送りした時点の `defer wrap ...` として出ている。
from instantale_modloader import patch_registry as R    # noqa: E402

R.reset()
R._entries.extend([
    (R.DEFERRED, "save_area_json:f", "225_probe", "save_area_json"),
    (R.DEFERRED, "save_area_json:g", "318_growth", "save_area_json"),
    (R.DEFERRED, "save_area_json:h", "225_probe", "save_area_json"),
    (R.DEFERRED, "__main__:A.b", "300_event", "__main__"),
])
report = R.format_report()
check("先送りの見出しは全件の数", "deferred (4): waiting for the module to be imported" in report,
      report)
check("モジュールごとに件数と MOD を1行",
      "  save_area_json: 3 hook(s) <- 225_probe, 318_growth" in report
      and "  __main__: 1 hook(s) <- 300_event" in report, report)
check("対象ごとには並べ直さない", not any("save_area_json:f" in line for line in report), report)
R.reset()

# MOD の `ctx.log` の INFO 行も、前の boot と同じ文面なら書かない。WARN は毎回書く。
with tempfile.TemporaryDirectory() as out:
    path = os.path.join(out, "modloader.log")
    saved = {key: ml._state.get(key) for key in
             ("log_path", "logged_before", "logged_now", "repeats")}
    try:
        ml._state.update(log_path=path, logged_before=set(), logged_now=set(), repeats=0)
        ctx = ml.ModContext(out, os.path.join(_ROOT, "runtime"))
        ml._roll_logged_lines()
        ctx.log("area move custom: walk=3d")
        ctx.log("注意", level="WARN")
        ml._roll_logged_lines()
        ctx.log("area move custom: walk=3d")
        ctx.log("注意", level="WARN")
        written = lines(path)
        check("ctx.log の INFO 行は前の boot と同じなら書かない",
              written.count("INFO  area move custom: walk=3d") == 1, written)
        check("ctx.log の WARN 行は毎回書く", written.count("WARN  注意") == 2, written)
    finally:
        for key, value in saved.items():
            ml._state[key] = value

# modfacility / modnpc の `registered` は、同じ持ち主・同じ世代・同じ層の数なら書かない。
# `snapshot of … taken` と関所の行は、中身が前に書いたものと同じなら書かない。
from instantale_modloader import modfacility, modnpc, patch as P    # noqa: E402

for framework, prefix in ((modfacility, "modfacility"), (modnpc, "modnpc")):
    framework.purge()
    framework._LAST_WRITTEN.clear()
    wrote = []
    saved_generation = getattr(P, "_generation", None)
    try:
        P.set_generation("gen-a")
        framework.register("331_x", key="k", write=wrote.append)
        framework.register("331_x", key="k", write=wrote.append)
        check("{}: 同じ世代で同じ登録は1行".format(prefix),
              sum("registered" in w for w in wrote) == 1, wrote)
        framework.register("229_y", framework.make_id("331_x", "k"), write=wrote.append)
        check("{}: 層が増えたら書く".format(prefix),
              sum("registered" in w for w in wrote) == 2, wrote)
        P.set_generation("gen-b")
        framework.register("331_x", key="k", write=wrote.append)
        check("{}: 世代が変わったら書く".format(prefix),
              sum("registered" in w for w in wrote) == 3, wrote)
        check("{}: 顔ぶれが変われば snapshot を書く".format(prefix),
              framework._changed("snapshot", ("a",)) and framework._changed("snapshot", ("a", "b")))
        check("{}: 同じ顔ぶれの snapshot は書かない".format(prefix),
              not framework._changed("snapshot", ("a", "b")))
    finally:
        P.set_generation(saved_generation)
        framework.purge()
        framework._LAST_WRITTEN.clear()

print()
if failures:
    print("FAILED: {}".format(len(failures)))
    for name in failures:
        print("  - " + name)
    sys.exit(1)
print("all ok")
