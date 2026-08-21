# -*- coding: utf-8 -*-
"""219_probe_crash_log.py をゲーム抜きで通す。

    python tools/tests/test_crash_log_probe.py

偽の `__main__.make_crash_log` を差し込む。
**偽ゲームは手元の記録に合わせてある**（`crash_log.txt` と
`out/live_crashes.log`。docs/GAME.md §1.4）:

  `__main__.datetime` はモジュール束縛（recon のスナップショット21件で不変）
  MAIN 経路は9件とも記録が書けている
  スレッド経路は `datetime.now()` で落ちる

どの枝が `instantale.py:200` なのかは読めないので、偽ゲームは
「`title` で枝が割れる」形にしてある。
probe が確かめるのは枝の正体ではなく、**落ちた呼び出しを見分けて、
差し替えれば記録が出来たのかを控えられるか**なので、
枝の割れ方そのものはテストの対象ではない。

確認するもの:

  通る呼び出し … `OK` と記録の頭が残る。ゲームには本体の戻り値がそのまま返る
  落ちる呼び出し … `FAILED` と落ちた行、続けて差し替えでのやり直しの結果が残る
  読み取り専用 … やり直しが通っても**元の例外を投げ直す**。
                 やり直しの結果はゲームへ渡らない
  後始末     … `__main__.datetime` は呼び出しの前後で同じもの。
                 やり直しが落ちた場合も戻る
  巻き添え無し … `datetime` と無関係な AttributeError はそのまま素通し。
                 やり直しもしない
  shim       … モジュールの名前（`timedelta` / `datetime`）を先に返し、
                 クラス側にしかない `now` はクラスへ落として引ける
  後段       … `write_crash_log_to_file` /
                 `should_skip_crash_log_server_send` /
                 `send_crash_log_to_server` は素通しで、値を変えない
"""
import datetime
import importlib.util
import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
RUNTIME_DIR = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir, "runtime"))
MODS_DIR = os.path.join(RUNTIME_DIR, "mods")
OUT_DIR = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir, "out", "test"))

if RUNTIME_DIR not in sys.path:
    sys.path.insert(0, RUNTIME_DIR)

import instantale_modloader as ml                      # noqa: E402

LOG_BASENAME = "crash_log_probe.log"


def find_mod(suffix):
    matches = sorted(name for name in os.listdir(MODS_DIR)
                     if name.endswith(suffix)
                     and os.path.isfile(os.path.join(MODS_DIR, name, "mod.json")))
    if not matches:
        raise SystemExit("cannot find *{} in {}".format(suffix, MODS_DIR))
    if len(matches) > 1:
        raise SystemExit("ambiguous: {} in {}".format(matches, MODS_DIR))
    folder = os.path.join(MODS_DIR, matches[0])
    with io.open(os.path.join(folder, "mod.json"), encoding="utf-8") as fh:
        entry = json.load(fh)["entry"]
    return os.path.join(folder, entry)


MOD = find_mod("_probe_crash_log")

failures = []
passed = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name
          + ((" -- " + str(detail)) if detail and not cond else ""))
    (passed if cond else failures).append(name)


def raises_attribute_error(fn):
    try:
        fn()
    except AttributeError:
        return True
    except Exception:
        return False
    return False


# ---------------------------------------------------------------- 偽ゲーム
MAIN = sys.modules["__main__"]

#: 本体の記録の書式（`crash_log.txt` の実物から）。
HEADER = "=" * 80


def make_crash_log(exc_type, exc_value, exc_traceback, title="CRASH"):
    """本体の記録を組む関数の代役。

    `title` がスレッドのものなら、モジュールに無い `now` を引いて落ちる。
    そうでなければモジュール越しにクラスを引くので通る。
    """
    module = MAIN.datetime
    if str(title).startswith("THREAD"):
        stamp = module.now().isoformat()          # ← 落ちる枝
    else:
        stamp = module.datetime.now().isoformat()
    return "\n".join([HEADER,
                      "{}: {}".format(title, stamp),
                      "game_version: 014",
                      HEADER,
                      "".join(["Traceback...\n"])])


def make_crash_log_other_attribute_error(*args, **kwargs):
    """`datetime` と無関係な AttributeError を出す代役。"""
    raise AttributeError("'NoneType' object has no attribute 'name'")


def make_crash_log_always_broken(*args, **kwargs):
    """差し替えても直らない代役（やり直しが失敗する側）。"""
    raise AttributeError("module 'datetime' has no attribute 'now'")


def make_crash_log_value_error(*args, **kwargs):
    """AttributeError 以外の落ち方をする代役。"""
    raise ValueError("something else went wrong")


class FakeCtx:
    def __init__(self, out_dir):
        self.out_dir = out_dir
        self.hooks = {}
        self.errors = []
        self.logs = []

    def out_path(self, *parts):
        path = os.path.join(self.out_dir, *parts)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        return path

    # ログは本物の `ctx.logger` をそのまま借りる（`315_` のテストと同じ理由）。
    _mod = None

    def logger(self, name, *, tag=None, stamp=True, label=None, cap=None):
        return ml.ModContext.logger(self, name, tag=tag, stamp=stamp,
                                    label=label, cap=cap)

    def log(self, msg, level="INFO"):
        self.logs.append((level, msg))

    def log_exc(self, msg):
        self.errors.append(msg)

    def wrap(self, target, **kw):
        def decorator(func):
            self.hooks[target] = func
            return func
        return decorator

    def superseded(self):
        return False              # テスト中に注入し直しは起きない


def load_mod(path=MOD, name="crash_log_probe_mod"):
    spec = importlib.util.spec_from_file_location(
        name, path, submodule_search_locations=[os.path.dirname(path)])
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    return module


def read_log(ctx):
    path = ctx.out_path(LOG_BASENAME)
    if not os.path.exists(path):
        return ""
    with io.open(path, encoding="utf-8") as fh:
        return fh.read()


def boom():
    """連鎖した例外を1つ作る（実機の `KeyError: '229'` と同じ形）。"""
    try:
        try:
            raise KeyError("229")
        except KeyError:
            raise KeyError("229")
    except KeyError as exc:
        return exc


def main():
    path = os.path.join(OUT_DIR, LOG_BASENAME)
    os.makedirs(OUT_DIR, exist_ok=True)
    if os.path.exists(path):
        os.remove(path)

    module = load_mod()

    # 偽ゲームを `__main__` に載せる。
    # probe は `sys.modules["__main__"]` から掴むので、ここが差し込み口になる。
    MAIN.datetime = datetime
    MAIN.make_crash_log = make_crash_log
    MAIN.write_crash_log_to_file = lambda crash_log: "written"
    MAIN.should_skip_crash_log_server_send = lambda *a: False
    MAIN.send_crash_log_to_server = lambda crash_log: "sent"

    ctx = FakeCtx(OUT_DIR)
    module.apply(ctx)

    hook = ctx.hooks.get("__main__:make_crash_log")
    check("make_crash_log に仕掛かる", hook is not None)
    if hook is None:
        return 1

    exc = boom()

    # -- 通る呼び出し ------------------------------------------------------
    print("\n[通る呼び出し（MAIN 経路）]")
    result = hook(make_crash_log, KeyError, exc, exc.__traceback__, "MAIN CRASH")
    check("本体の戻り値がそのまま返る",
          isinstance(result, str) and "MAIN CRASH" in result, repr(result)[:120])
    log = read_log(ctx)
    check("OK が残る", "  OK " in log)
    check("記録の頭が残る", "head:" in log)
    check("連鎖の数が残る", "chain=2" in log, log)
    check("datetime の正体が残る", "datetime = <module 'datetime'" in log, log)

    # -- 落ちる呼び出し ----------------------------------------------------
    print("\n[落ちる呼び出し（スレッド経路）]")
    before = MAIN.datetime
    raised = None
    try:
        hook(make_crash_log, KeyError, exc, exc.__traceback__,
             "THREAD CRASH: Thread-1225 (execute)")
    except AttributeError as caught:
        raised = caught
    check("元の例外を投げ直す（ゲームの挙動は変わらない）",
          raised is not None and "has no attribute 'now'" in str(raised), raised)
    check("datetime を戻す", MAIN.datetime is before, MAIN.datetime)

    log = read_log(ctx)
    check("FAILED が残る", "  FAILED " in log)
    check("落ちた行が残る", "at test_crash_log_probe.py:" in log, log[-400:])
    check("やり直しが通ったことが残る",
          "RETRY with the shim -> OK" in log, log[-400:])
    check("やり直しで組んだ記録の頭が残る",
          "THREAD CRASH: Thread-1225 (execute)" in log, log[-400:])

    # -- 巻き添えにしない --------------------------------------------------
    print("\n[datetime と無関係な AttributeError]")
    marker = len(read_log(ctx))
    raised = None
    try:
        hook(make_crash_log_other_attribute_error, KeyError, exc,
             exc.__traceback__, "THREAD CRASH: Thread-2 (execute)")
    except AttributeError as caught:
        raised = caught
    check("そのまま投げ直す",
          raised is not None and "'NoneType' object" in str(raised), raised)
    tail = read_log(ctx)[marker:]
    check("やり直しをしない", "RETRY" not in tail, tail)
    check("FAILED も書かない", "FAILED" not in tail, tail)
    check("素通ししたことは残る（見出しだけで終わらせない）",
          "RAISED" in tail and "素通し" in tail, tail)

    # -- AttributeError 以外 -----------------------------------------------
    print("\n[AttributeError 以外の落ち方]")
    marker = len(read_log(ctx))
    raised = None
    try:
        hook(make_crash_log_value_error, KeyError, exc, exc.__traceback__,
             "THREAD CRASH: Thread-4 (execute)")
    except ValueError as caught:
        raised = caught
    check("そのまま投げ直す", raised is not None, raised)
    tail = read_log(ctx)[marker:]
    check("素通ししたことは残る", "RAISED" in tail and "ValueError" in tail, tail)
    check("やり直しをしない", "RETRY" not in tail, tail)

    # -- 差し替えても直らない場合 ------------------------------------------
    print("\n[差し替えても直らない場合]")
    before = MAIN.datetime
    marker = len(read_log(ctx))
    raised = None
    try:
        hook(make_crash_log_always_broken, KeyError, exc, exc.__traceback__,
             "THREAD CRASH: Thread-3 (execute)")
    except AttributeError as caught:
        raised = caught
    check("元の例外を投げ直す", raised is not None, raised)
    check("落ちても datetime を戻す", MAIN.datetime is before, MAIN.datetime)
    tail = read_log(ctx)[marker:]
    check("やり直しが通らなかったことが残る",
          "RETRY with the shim -> still failed" in tail, tail)

    # -- shim 単体 ---------------------------------------------------------
    print("\n[shim]")
    shim = module._SHIM
    check("モジュールの名前を先に返す（timedelta）",
          shim.timedelta is datetime.timedelta)
    check("モジュールの名前を先に返す（datetime）",
          shim.datetime is datetime.datetime)
    check("クラス側にしかない now を引ける",
          isinstance(shim.now(), datetime.datetime))
    check("無い名前は AttributeError",
          raises_attribute_error(lambda: shim.no_such_name))

    # -- 後段は素通し ------------------------------------------------------
    print("\n[後段]")
    for target, args, expected in (
            ("__main__:write_crash_log_to_file", ("text",), "written"),
            ("__main__:should_skip_crash_log_server_send",
             (KeyError, exc, exc.__traceback__), False),
            ("__main__:send_crash_log_to_server", ("text",), "sent")):
        step = ctx.hooks.get(target)
        check("{} に仕掛かる".format(target.rpartition(":")[2]), step is not None)
        if step is None:
            continue
        got = step(getattr(MAIN, target.rpartition(":")[2]), *args)
        check("{} は値を変えない".format(target.rpartition(":")[2]),
              got == expected, got)

    check("計測の中で例外を握り潰していない", not ctx.errors, ctx.errors)

    print("\n{} check(s), {} failure(s)".format(
        len(passed) + len(failures), len(failures)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
