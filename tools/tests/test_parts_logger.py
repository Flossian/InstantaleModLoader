# -*- coding: utf-8 -*-
"""`ctx.logger` の span / unrepeated / cap_notice と `ctx.once` をゲーム抜きで通す。

    python tools/tests/test_parts_logger.py

  既定     … 引数を足す前の答え（素の行・tag・stamp・cap・dedup・dedup と cap の併用）が変わらない
  unrepeated … 直前でなくても、この span で書いた本文は書かない。apply の span は関数ごと
  injection … 遅れて当て直す boot（同じモジュールの別の apply）をまたいで cap の数と覚えが残る。
              MOD・ファイル名・span_key ごとに別。手で注入し直す（ローダを読み直す）と空に戻る
  cap_notice … cap を超えた最初の1行の代わりに1回だけ書く。枠も覚えも食わない
  span の誤り … 知らない値は WARN を残して apply の span で動く
  once     … 鍵ごとに1回。鍵を省くと本文が鍵。prefix・limit・forget・store・書けないときの戻り値
"""
import importlib
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


def read(path):
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as fh:
        return [line.rstrip("\n") for line in fh]


def body(line):
    """時刻（`[...] `）を外した本文。"""
    return line.split("] ", 1)[1] if line.startswith("[") and "] " in line else line


with tempfile.TemporaryDirectory() as out:
    loader_log = os.path.join(out, "modloader.log")
    saved_log_path = ml._state.get("log_path")
    saved_spans = dict(ml._LOGGER_SPANS)
    ml._state["log_path"] = loader_log
    ml._LOGGER_SPANS.clear()
    try:
        ctx = ml.ModContext(out, os.path.join(_ROOT, "runtime"))
        ctx._mod = "900_test"

        # ---------------------------------------------------------- 既定は前と同じ
        plain = ctx.logger("plain.log")
        plain("a")
        plain("a\n")
        rows = read(os.path.join(out, "plain.log"))
        check("既定: 時刻付きで毎回書く", len(rows) == 2 and rows[0].startswith("[")
              and [body(r) for r in rows] == ["a", "a"], rows)

        tagged = ctx.logger("tag.log", tag="[FIX]", stamp=False)
        tagged("x")
        check("既定: tag は逐語で前に付く", read(os.path.join(out, "tag.log")) == ["[FIX] x"],
              read(os.path.join(out, "tag.log")))

        capped = ctx.logger("cap.log", cap=2, stamp=False)
        for text in ("1", "2", "3", "4"):
            capped(text)
        check("既定: cap で打ち切り、知らせは書かない",
              read(os.path.join(out, "cap.log")) == ["1", "2"], read(os.path.join(out, "cap.log")))

        zero = ctx.logger("zero.log", cap=0, stamp=False)
        zero("z")
        check("既定: cap=0 は何も書かない", read(os.path.join(out, "zero.log")) == [])

        both = ctx.logger("both.log", dedup=True, cap=2, stamp=False)
        for text in ("a", "a", "b", "b", "a", "c"):
            both(text)
        check("既定: dedup で落ちた行は cap の枠を食わない",
              read(os.path.join(out, "both.log")) == ["a", "b"], read(os.path.join(out, "both.log")))

        flip = ctx.logger("flip.log", dedup=True, stamp=False)
        for text in ("a", "b", "a", "b"):
            flip(text)
        check("既定: dedup は直前とだけ比べる（交互なら毎回書く）",
              read(os.path.join(out, "flip.log")) == ["a", "b", "a", "b"])

        again = ctx.logger("cap.log", cap=2, stamp=False)
        again("5")
        check("既定: apply の span は関数ごとに数え直す",
              read(os.path.join(out, "cap.log")) == ["1", "2", "5"], read(os.path.join(out, "cap.log")))
        check("既定: 覚えをモジュールに置かない", ml._LOGGER_SPANS == {}, ml._LOGGER_SPANS)

        # ---------------------------------------------------------- unrepeated
        uniq = ctx.logger("uniq.log", unrepeated=True, stamp=False)
        for text in ("a", "b", "a", "c", "b"):
            uniq(text)
        check("unrepeated: 前に書いたどの本文とも比べる",
              read(os.path.join(out, "uniq.log")) == ["a", "b", "c"], read(os.path.join(out, "uniq.log")))
        uniq2 = ctx.logger("uniq.log", unrepeated=True, stamp=False)
        uniq2("a")
        check("unrepeated: apply の span は新しい関数でまた書く",
              read(os.path.join(out, "uniq.log"))[-1] == "a", read(os.path.join(out, "uniq.log")))

        stamped = ctx.logger("stamped.log", unrepeated=True, tag="[T]")
        stamped("same")
        stamped("same")
        check("unrepeated: 時刻と印を付ける前の本文で比べる",
              [body(r) for r in read(os.path.join(out, "stamped.log"))] == ["[T] same"])

        mixed = ctx.logger("mixed.log", unrepeated=True, cap=2, stamp=False)
        for text in ("a", "a", "b", "c"):
            mixed(text)
        check("unrepeated: 書かなかった行は cap の枠を食わない",
              read(os.path.join(out, "mixed.log")) == ["a", "b"], read(os.path.join(out, "mixed.log")))

        # ---------------------------------------------------------- cap_notice
        noted = ctx.logger("notice.log", cap=2, cap_notice="... 以降は書かない", tag="[N]",
                           stamp=False)
        for text in ("1", "2", "3", "4", "5"):
            noted(text)
        check("cap_notice: 超えた最初の1行の代わりに1回だけ、tag 付きで",
              read(os.path.join(out, "notice.log")) == ["[N] 1", "[N] 2", "[N] ... 以降は書かない"],
              read(os.path.join(out, "notice.log")))
        unlimited = ctx.logger("unlimited.log", cap_notice="never", stamp=False)
        for text in ("1", "2"):
            unlimited(text)
        check("cap_notice: cap が無ければ出ない", read(os.path.join(out, "unlimited.log")) == ["1", "2"])

        # ---------------------------------------------------------- span="injection"
        # 遅れて当て直す boot は同じモジュールの boot() から apply を呼び直す。ctx は boot ごとに新しい。
        boot1 = ml.ModContext(out, os.path.join(_ROOT, "runtime"))
        boot1._mod = "411_x"
        note1 = boot1.logger("inj.log", cap=3, cap_notice="... 件数だけ数える", span="injection",
                             unrepeated=True, stamp=False)
        for text in ("installed", "hit 1", "installed"):
            note1(text)
        boot2 = ml.ModContext(out, os.path.join(_ROOT, "runtime"))
        boot2._mod = "411_x"
        note2 = boot2.logger("inj.log", cap=3, cap_notice="... 件数だけ数える", span="injection",
                             unrepeated=True, stamp=False)
        for text in ("installed", "hit 2", "hit 3", "hit 4", "hit 5"):
            note2(text)
        check("injection: boot をまたいで覚えと cap の数が残り、知らせは1回",
              read(os.path.join(out, "inj.log"))
              == ["installed", "hit 1", "hit 2", "... 件数だけ数える"],
              read(os.path.join(out, "inj.log")))
        note1("hit 6")
        check("injection: 前の boot の関数も同じ覚えを使う（知らせは2度出ない）",
              read(os.path.join(out, "inj.log"))[-1] == "... 件数だけ数える"
              and len(read(os.path.join(out, "inj.log"))) == 4)

        other = boot2.logger("inj.log", cap=1, span="injection", span_key="detail", stamp=False)
        other("detail 1")
        check("injection: span_key が違えば別に数える",
              read(os.path.join(out, "inj.log"))[-1] == "detail 1")
        boot2._mod = "412_y"
        neighbour = boot2.logger("inj.log", cap=3, span="injection", unrepeated=True, stamp=False)
        neighbour("installed")
        check("injection: MOD が違えば別に覚える", read(os.path.join(out, "inj.log"))[-1] == "installed")
        check("injection: 鍵は (MOD, name, span_key)",
              set(ml._LOGGER_SPANS) == {("411_x", "inj.log", None), ("411_x", "inj.log", "detail"),
                                        ("412_y", "inj.log", None)}, set(ml._LOGGER_SPANS))

        dd1 = boot1.logger("dd.log", dedup=True, span="injection", stamp=False)
        dd1("result A")
        dd2 = boot2.logger("dd.log", dedup=True, span="injection", stamp=False)
        boot2._mod = "411_x"
        dd3 = boot2.logger("dd.log", dedup=True, span="injection", stamp=False)
        dd3("result A")
        dd2("result A")
        check("injection: dedup の直前の本文も span に置く（412_y の分は別）",
              read(os.path.join(out, "dd.log")) == ["result A", "result A"],
              read(os.path.join(out, "dd.log")))

        outside = ml.ModContext(out, os.path.join(_ROOT, "runtime"))
        free = outside.logger("free.log", span="injection", label="tool", stamp=False)
        free("x")
        check("injection: apply の外では label で鍵を作る", ("tool", "free.log", None) in ml._LOGGER_SPANS)

        # ---------------------------------------------------------- span の誤り
        before = len(read(loader_log))
        odd = ctx.logger("odd.log", span="boot", cap=1, stamp=False)
        odd("1")
        odd("2")
        warned = read(loader_log)[before:]
        check("span の誤り: WARN を残す",
              any("WARN" in r and "unknown logger span" in r for r in warned), warned)
        check("span の誤り: apply の span で動く", read(os.path.join(out, "odd.log")) == ["1"]
              and not any(k[1] == "odd.log" for k in ml._LOGGER_SPANS))

        # ---------------------------------------------------------- once
        lines = []
        warn_once = ctx.once(lines.append, prefix="WARN ")
        check("once: 初めての鍵は書いて True", warn_once(("area", 1), "no area 1") is True)
        check("once: 同じ鍵は書かずに False", warn_once(("area", 1), "no area 1 again") is False)
        warn_once(("area", 2), "no area 2")
        check("once: prefix は逐語で前に付く", lines == ["WARN no area 1", "WARN no area 2"], lines)
        warn_once.forget(("area", 1))
        check("once: forget した鍵はまた書く", warn_once(("area", 1), "back") is True
              and lines[-1] == "WARN back", lines)
        warn_once.forget()
        check("once: 鍵なしの forget は全部落とす", warn_once.seen == set())
        check("once: 知らない鍵の forget は何もしない", warn_once.forget("none") is None)

        texts = []
        note_once = ctx.once(texts.append, limit=2)
        for text in ("a", "b", "a", "b"):
            note_once(text)
        check("once: 鍵を省くと本文が鍵（交互でも1回ずつ）", texts == ["a", "b"], texts)
        note_once("c")
        check("once: limit に達したら覚えを捨ててから覚え直す", note_once.seen == {"c"}, note_once.seen)
        note_once("a")
        check("once: 捨てた後はまた書く", texts == ["a", "b", "c", "a"], texts)

        shared = set()
        first = ctx.once(texts.append, store=shared)
        first("k", "first")
        second = ctx.once(texts.append, store=shared)
        check("once: store を渡すと関数をまたいで覚える（注入をまたぐ置き場）",
              second("k", "second") is False and "k" in shared and second.seen is shared)

        def broken(_text):
            raise OSError("disk")

        before = len(read(loader_log))
        failing = ctx.once(broken)
        check("once: write が投げたら False", failing("k", "x") is False)
        check("once: 投げても覚えは残す（毎回書きに行かない）", failing("k", "x") is False
              and "k" in failing.seen)
        check("once: 投げたことを modloader.log に残す",
              any("once write failed" in r for r in read(loader_log)[before:]), read(loader_log)[before:])

        with_logger = ctx.once(ctx.logger("once.log", stamp=False), prefix="WARN ")
        with_logger("x", "logged")
        with_logger("x", "logged")
        check("once: ctx.logger の戻り値へ書ける", read(os.path.join(out, "once.log")) == ["WARN logged"])

        # ---------------------------------------------------------- 手で注入し直す
        # ブートストラップはローダを sys.modules から落として読み直す（tools/injector.py）。
        # 遅れて当て直す boot はモジュールを読み直さない（`_deferred_loop` が同じモジュールの boot() を呼ぶ）。
        check("読み直し: 読み直す前は覚えがある", len(ml._LOGGER_SPANS) > 0)
        saved_modules = {k: v for k, v in sys.modules.items()
                         if k == "instantale_modloader" or k.startswith("instantale_modloader.")}
        try:
            for name in saved_modules:
                del sys.modules[name]
            fresh = importlib.import_module("instantale_modloader")
            check("読み直し: 別のモジュールになる", fresh is not ml)
            check("読み直し: 覚えは空から始まる", fresh._LOGGER_SPANS == {}, fresh._LOGGER_SPANS)
            fresh._state["log_path"] = loader_log
            ctx3 = fresh.ModContext(out, os.path.join(_ROOT, "runtime"))
            ctx3._mod = "411_x"
            note3 = ctx3.logger("inj.log", cap=3, cap_notice="... 件数だけ数える", span="injection",
                                unrepeated=True, stamp=False)
            note3("installed")
            check("読み直し: 同じ行をもう一度書く（世代送りされた新しいログの頭へ）",
                  read(os.path.join(out, "inj.log"))[-1] == "installed")
        finally:
            for name in [k for k in sys.modules
                         if k == "instantale_modloader" or k.startswith("instantale_modloader.")]:
                del sys.modules[name]
            sys.modules.update(saved_modules)
    finally:
        ml._state["log_path"] = saved_log_path
        ml._LOGGER_SPANS.clear()
        ml._LOGGER_SPANS.update(saved_spans)

print()
if failures:
    print("FAILED: {}".format(len(failures)))
    for name in failures:
        print("  - " + name)
    sys.exit(1)
print("all ok")
