# -*- coding: utf-8 -*-
r"""退行の見張り: 注入がゲームの `__main__` を汚して、本体のクラッシュ記録を壊していないか。

##### 何があったか

本体の記録処理が、記録を書く前に自分で落ちていた。

    File "instantale.py", line 288, in report_crash
    File "instantale.py", line 200, in make_crash_log
    AttributeError: module 'datetime' has no attribute 'now'

`make_crash_log` は文字列を組んで返すだけの関数で、その後段が
`crash_log.txt` への書き込みと送信を持っている。

    report_crash
      ├ make_crash_log(...) -> str          ここで落ちると以降は走らない
      ├ write_crash_log_to_file(crash_log)  CRASH_LOG_PATH
      ├ should_skip_crash_log_server_send(...)
      └ send_crash_log_to_server(crash_log) CRASH_REPORT_URL

素のゲームの不具合ではなく、こちら側が原因だった（VERIFICATION.md §3.33）。
注入の流し込みがゲームの `__main__` の辞書で `import datetime` を実行し、
本体のクラス束縛（`from datetime import datetime`）をモジュールで上書きしていた。
流し込みを関数で包んで直した。

この probe はその切り分けに使った道具で、決着した後も**同じ形の退行の見張り**として置いている。
注入や MOD がまた `__main__` の名前を上書きすれば、ここに `FAILED` が出る。

##### 何を録るか

`make_crash_log` を包んで、1回の呼び出しごとに次を控える。

  * 呼ばれたスレッドと `title`（型と値。本体が何を渡しているか）
  * 例外の種類と、`__cause__` / `__context__` を辿った連鎖の数
  * その瞬間の `__main__.datetime` の正体
  * 呼び出し元の経路と、通過用の包みを除いた MOD の枠が呼び出しの起点にあるか
  * 組み上がった文字列の頭（通った枝の見分けが付く）

落ちた場合はさらに、`datetime` を差し替えて**もう一度だけ**素の関数を呼び、
記録が出来上がるのかを控える。
`make_crash_log` は文字列を組んで返すだけで、書き込みも送信も後段なので、
ここでやり直しても二重には残らない。

##### 読み方

直った後の正常な形は、1件ごとに `datetime = <class 'datetime.datetime'>`・`OK`・
後段3つ（`write_crash_log_to_file` / `should_skip...` / `send_crash_log_to_server`）の行が並ぶ。
`FAILED` と `datetime = <module 'datetime' ...>` が出たら退行で、`__main__` を上書きしている
注入か MOD を探す。
`mods in path = yes` は、`orig` をそのまま呼ぶだけの包み（`001_crash_recorder` の `report_crash` など）を
除いても、呼び出しの起点に MOD の枠があった回（版2）。

##### ゲームは変えない

やり直しの結果は**捨てて、元の例外をそのまま投げ直す**。
本体から見た挙動は変わらない。
差し替えも `finally` で必ず戻す。

##### 引き金

スレッドクラッシュを確実に起こせる場面は VERIFICATION.md §3.33（直った後の実機）と
GAME.md §2.23（`world_dict` と `save_data_dict` の食い違い）にある。

版2: 決着（VERIFICATION.md §3.33）に合わせて、説明を退行の見張りとして書き直した。
あわせて次を直した。

- `mods in path` が46件中46件で yes だった。`001_crash_recorder` が `report_crash` を包んでいて、
  その層が必ず経路に挟まるため。
  `orig` をそのまま呼ぶだけの包みの層・ローダ本体・自分を除き、最も内側から続くゲーム側の枠を抜けた直後の1枚が
  MOD かで決める（`214_` の版6と同じ判定。`mod_caller`）
- 経路は `traceback.extract_stack()` で組んでいて、枠ごとにソースの行を読みに行っていた。
  `traceback.walk_stack` を `lookup_lines=False` で読む（記録の形は同じ）
- 包みを全部 `safe=True` にした。
  やり直しは `orig` ではなく、包みを剥がした素の関数（`patch.original_of`）で行う。
  `orig` で呼び直すと、`safe=True` の守りが「最後に呼んだ結果」としてやり直しの答えを控え、
  元の例外を投げ直したときにその答えをゲームへ返してしまう（`patch._guard` の説明）
"""

import datetime as _datetime
import os
import sys
import threading
import time
import traceback

from instantale_modloader import frames, patch

LOG_BASENAME = "crash_log_probe.log"

#: `__main__.make_crash_log` が現れるのを待つ間隔と、諦めるまでの時間（秒）。
#: `001_crash_recorder` と同じ理由で待つ。
#: `__main__` は常に `sys.modules` に在るのでローダの保留には載せられず、
#: 「モジュールは在るが属性がまだ無い」はここで待つしかない。
WATCH_POLL = 2.0
WATCH_SECONDS = 600.0

#: 経路として控える枠の数（内側から）。
ROUTE_DEPTH = 8

#: 組み上がった記録から控える先頭の行数。
HEAD_LINES = 3

SNIP = 120

#: 「MOD の枠か」の判定の足場（版2。`214_` の版6と同じ）。
#: MOD の置き場（`runtime/mods` と `local/`）は `frames.is_ours` で見て、自分自身とローダ本体は数えない。
MY_FILE = os.path.normcase(os.path.abspath(__file__)).lower()
LOADER_DIR = os.path.join(
    os.path.normcase(os.path.dirname(os.path.abspath(frames.__file__))).lower(), "")
#: 呼び出しの起点を探して辿る枠の上限。
CALLER_SCAN_DEPTH = 80


class _DatetimeShim(object):
    """`datetime` モジュールとして振る舞い、無い名前は `datetime.datetime` へ落とす。

    `datetime.datetime` / `timedelta` / `timezone` を引く他の経路が
    差し替えの最中に走っても壊れないように、モジュールを先に見る。
    足りないのは `now` のような**クラス側にしかない名前**だけなので、
    取りこぼしたときだけクラスへ落とせば両方が通る。
    """

    def __getattr__(self, name):
        try:
            return getattr(_datetime, name)
        except AttributeError:
            return getattr(_datetime.datetime, name)

    def __repr__(self):
        return "<probe datetime shim>"


_SHIM = _DatetimeShim()


def _chain_length(exc_value):
    """`__cause__` / `__context__` を辿った例外の数。輪になっていても止まる。"""
    count = 0
    seen = set()
    current = exc_value
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        count += 1
        current = getattr(current, "__cause__", None) or getattr(current, "__context__", None)
    return count


def _norm(filename):
    try:
        return os.path.normcase(os.path.abspath(filename)).lower()
    except Exception:
        return ""


def passes_to_orig(frame, inner):
    """MOD の枠 `frame` が、受け取った `orig` を呼んでいる最中か（＝通過用の包みの層か）。

    `@ctx.wrap` の層は第1引数の `orig` を呼んで内側へ降りる。
    1つ内側の枠 `inner` がその `orig` の実行中なら、この枠は包みの層で、呼び出しの起点ではない。
    包みの本体からゲームの別の関数を呼んでいる最中なら `inner` は `orig` ではないので、起点として数える。
    """
    if inner is None:
        return False
    code = frame.f_code
    if code.co_argcount < 1:
        return False
    try:
        orig = frame.f_locals.get(code.co_varnames[0])
    except Exception:
        return False
    if orig is None or not callable(orig):
        return False
    original = getattr(orig, "__original__", None)
    if inner.f_code is getattr(orig, "__code__", None):
        if original is None:
            return True
        # ローダの素の包み（`patch.py` の `wrapper` / `guarded`）は同じ種類どうしでコードを共有するので、
        # 包んでいる中身（`old`）まで同じかで見分ける。
        try:
            return inner.f_locals.get("old") is original
        except Exception:
            return False
    # ゲームの関数は Nuitka で組まれていて、関数とフレームのコードが同じ物とは限らない。
    # 包みが素の関数を直に呼んでいる形だけ、名前で見る。
    return (original is None and not frames.is_ours(inner.f_code.co_filename)
            and getattr(orig, "__name__", None) == inner.f_code.co_name)


def mod_caller(start, own_file=MY_FILE):
    """呼び出しの起点が MOD なら、その枠の説明を返す。ゲーム側なら None（版2）。

    `start` から外へ辿る。
    自分のファイル・ローダ本体（`patch.py` など）・`orig` を呼んでいる最中の包みの層は飛ばす。
    最も内側から続くゲーム側の枠を抜けた直後の1枚が MOD の枠なら、その MOD が呼んだとみなす。
    ゲーム側の枠より先に MOD の枠に当たったとき（MOD が直に呼んだ）も MOD とみなす。
    抜けた直後の1枚がゲーム側なら、ゲームの呼び出しとみなして止める。
    """
    inner = None
    frame = start
    in_game = left_game = False
    depth = 0
    while frame is not None and depth < CALLER_SCAN_DEPTH:
        depth += 1
        code = frame.f_code
        name = code.co_filename
        if frames.is_ours(name):
            path = _norm(name)
            if (path == own_file or path.startswith(LOADER_DIR)
                    or passes_to_orig(frame, inner)):
                if in_game:
                    left_game = True
            else:
                return "{} ({}:{})".format(code.co_name, name, frame.f_lineno)
        else:
            if left_game:
                return None
            in_game = True
        inner = frame
        frame = frame.f_back
    return None


def _route(skip_innermost=1):
    """呼び出し元の経路を1行にする。MOD の枠には印を付ける。

    戻り値は (経路の文字列, 呼び出しの起点にある MOD の枠の説明か None)。
    2つ目は `mod_caller` の答えで、通過するだけの包みの層は数えない（版2）。

    枠は `traceback.walk_stack` を内側から `lookup_lines=False` で読む（版2）。
    版1の `traceback.extract_stack()` は枠ごとにソースの行を読みに行っていた。
    読む枠の数は、落とす内側の枠と `ROUTE_DEPTH` の和で打ち切る。
    並べ方（外側から内側へ）と1枠の書き方は版1と同じ。
    """
    here = sys._getframe()
    try:
        # 内側から並ぶので、外側から内側の順に直す（`extract_stack` と同じ向き）。
        stack = list(traceback.StackSummary.extract(
            traceback.walk_stack(here), limit=ROUTE_DEPTH + skip_innermost + 1,
            lookup_lines=False))
        stack.reverse()
    except Exception:
        stack = None
    try:
        found = mod_caller(here.f_back)
    except Exception:
        found = None
    # 自分の枠を握ったままにしない（枠とローカル変数の循環参照になる）。
    del here
    if stack is None:
        return "<unavailable>", found
    # 一番内側はこの関数自身とその呼び出し元（probe のラッパ）なので落とす。
    stack = stack[:-(skip_innermost + 1)] if skip_innermost + 1 <= len(stack) else []
    stack = stack[-ROUTE_DEPTH:]
    parts = []
    for entry in stack:
        name = entry.filename.rsplit("\\", 1)[-1].rsplit("/", 1)[-1]
        mark = "[mod]" if frames.is_ours(entry.filename) else ""
        parts.append("{}:{} {}{}".format(name, entry.lineno, entry.name, mark))
    return " > ".join(parts), found


def _where(exc):
    """例外が上がった一番内側の場所。"""
    tb = getattr(exc, "__traceback__", None)
    last = None
    while tb is not None:
        last = tb
        tb = tb.tb_next
    if last is None:
        return "<no traceback>"
    code = last.tb_frame.f_code
    name = code.co_filename.rsplit("\\", 1)[-1].rsplit("/", 1)[-1]
    return "{}:{} in {}".format(name, last.tb_lineno, code.co_name)


def _head(text):
    """組み上がった記録の頭。通った枝の見分けに使う。"""
    if not isinstance(text, str):
        return "<{} を返した>".format(type(text).__name__)
    lines = [line for line in text.splitlines() if line.strip()][:HEAD_LINES]
    return " / ".join(frames.short(line, SNIP) for line in lines)


def apply(ctx):
    write = ctx.logger(LOG_BASENAME)

    # 差し替えの最中に別のスレッドがもう1件落ちても、
    # 戻し忘れと二重の差し替えが起きないようにする。
    lock = threading.Lock()
    state = {"calls": 0, "failed": 0, "recovered": 0}

    def install(main):

        def trial(args, kwargs):
            """`datetime` を差し替えて、もう一度だけ組ませてみる。

            結果は返すだけで、呼び出し側は捨てる（ゲームには渡さない）。
            呼ぶのは包みを全部剥がした素の関数（版2）。
            `orig` で呼び直すと、`safe=True` の守りがやり直しの答えを「最後に呼んだ結果」として控え、
            元の例外を投げ直したときにその答えをゲームへ返す（`patch._guard` の説明）。
            外側の MOD の層（`001_` など）も二度は通さない。
            """
            bare = patch.original_of(getattr(main, "make_crash_log", None))
            if not callable(bare):
                return False, RuntimeError("no bare make_crash_log to retry")
            with lock:
                real = getattr(main, "datetime", _datetime)
                setattr(main, "datetime", _SHIM)
                try:
                    return True, bare(*args, **kwargs)
                except Exception as exc:
                    return False, exc
                finally:
                    # 差し替えたままにしない。
                    # ここで戻らないと、以降の `datetime` 参照が全部この shim を通る。
                    setattr(main, "datetime", real)

        @ctx.wrap("__main__:make_crash_log", required=False, safe=True)
        def make_crash_log(orig, *args, **kwargs):
            # 記録の失敗でクラッシュ処理を巻き込まない。
            # 控えは全部 try で囲み、失敗してもログに残すだけにする。
            try:
                exc_value = frames.arg(args, kwargs, "exc_value", 1)
                title = frames.arg(args, kwargs, "title", 3)
                route, found = _route()
                state["calls"] += 1
                write("--- make_crash_log #{} ---".format(state["calls"]))
                write("  thread   = {}".format(threading.current_thread().name))
                write("  title    = {} {}".format(
                    type(title).__name__, frames.short(repr(title), SNIP)))
                write("  exc      = {} chain={}".format(
                    getattr(frames.arg(args, kwargs, "exc_type", 0), "__name__", "?"),
                    _chain_length(exc_value)))
                bound = getattr(main, "datetime", None)
                loaded = sys.modules.get("datetime")
                write("  datetime = {}".format(frames.short(repr(bound), SNIP)))
                # `__main__` が持っているものと、いま import できるものが
                # 同じ実体かどうか。違っていれば、束縛が誰かに差し替わっている。
                write("  datetime is sys.modules['datetime'] = {} | has now = {}".format(
                    bound is loaded, "now" in dir(bound) if bound is not None else "?"))
                write("  boot     = {}".format(getattr(ctx, "generation", "?")))
                # 版2: 通過するだけの包みの層を除いた判定。yes のときは起点の枠を添える。
                write("  mods in path = {}".format(
                    "yes ({})".format(found) if found else "no"))
                write("  route    = {}".format(route))
            except Exception:
                ctx.log_exc("crash log probe: recording the call failed")

            try:
                result = orig(*args, **kwargs)
            except AttributeError as exc:
                text = str(exc)
                if "datetime" not in text:
                    # 別件。素通しするが、見出しだけ残して結末が無いと
                    # 「録り損ねた」のか「別の落ち方だった」のかが読めない。
                    try:
                        write("  RAISED   {}: {} (datetime とは無関係。素通し)".format(
                            type(exc).__name__, frames.short(text, SNIP)))
                        write("    at {}".format(_where(exc)))
                    except Exception:
                        ctx.log_exc("crash log probe: recording the pass-through failed")
                    raise
                try:
                    state["failed"] += 1
                    write("  FAILED   {}: {}".format(type(exc).__name__, text))
                    write("    at {}".format(_where(exc)))
                except Exception:
                    ctx.log_exc("crash log probe: recording the failure failed")

                try:
                    ok, outcome = trial(args, kwargs)
                    if ok:
                        state["recovered"] += 1
                        write("  RETRY with the shim -> OK, {} chars".format(
                            len(outcome) if isinstance(outcome, str) else -1))
                        write("    head: {}".format(_head(outcome)))
                    else:
                        write("  RETRY with the shim -> still failed: {}: {}".format(
                            type(outcome).__name__, frames.short(str(outcome), SNIP)))
                        write("    at {}".format(_where(outcome)))
                except Exception:
                    ctx.log_exc("crash log probe: the retry itself failed")

                # ここが読み取り専用の要。
                # やり直しが通っていても、その結果は渡さずに元の例外を投げ直す。
                # 本体から見た挙動は今と変わらない。
                raise
            except Exception as exc:
                # `datetime` の取り違え以外の落ち方。素通しする。
                try:
                    write("  RAISED   {}: {} (素通し)".format(
                        type(exc).__name__, frames.short(str(exc), SNIP)))
                    write("    at {}".format(_where(exc)))
                except Exception:
                    ctx.log_exc("crash log probe: recording the pass-through failed")
                raise

            try:
                write("  OK       {} chars".format(
                    len(result) if isinstance(result, str) else -1))
                write("    head: {}".format(_head(result)))
            except Exception:
                ctx.log_exc("crash log probe: recording the result failed")
            return result

        # -- 後段（通れば記録が残り、送信まで行く経路） ----------------------
        # 直った後（VERIFICATION.md §3.33）は、`make_crash_log` が通るたびにこの3つが並ぶ。
        # 並ばなくなったら、それ自体が退行の印になる。

        @ctx.wrap("__main__:write_crash_log_to_file", required=False, safe=True)
        def write_crash_log_to_file(orig, *args, **kwargs):
            try:
                text = frames.arg(args, kwargs, "crash_log", 0)
                write("  -> write_crash_log_to_file: {} chars".format(
                    len(text) if isinstance(text, str) else -1))
            except Exception:
                ctx.log_exc("crash log probe: recording the file write failed")
            return orig(*args, **kwargs)

        @ctx.wrap("__main__:should_skip_crash_log_server_send", required=False,
                  safe=True)
        def should_skip_crash_log_server_send(orig, *args, **kwargs):
            verdict = orig(*args, **kwargs)
            try:
                write("  -> should_skip_crash_log_server_send = {!r}".format(verdict))
            except Exception:
                ctx.log_exc("crash log probe: recording the send verdict failed")
            return verdict

        @ctx.wrap("__main__:send_crash_log_to_server", required=False, safe=True)
        def send_crash_log_to_server(orig, *args, **kwargs):
            # ここに来たことだけを控えて、そのまま下へ通す。
            # 送るか止めるかを決めるのはこの probe ではない
            # （`001_crash_recorder` が注入中は止める）。
            try:
                text = frames.arg(args, kwargs, "crash_log", 0)
                write("  -> send_crash_log_to_server: {} chars "
                      "(送るかどうかは下の層が決める)".format(
                          len(text) if isinstance(text, str) else -1))
            except Exception:
                ctx.log_exc("crash log probe: recording the send failed")
            return orig(*args, **kwargs)

        ctx.log("crash log probe: armed on make_crash_log and the 3 steps after it "
                "| log {}".format(ctx.out_path(LOG_BASENAME)))

    # **在るとは限らない。**
    # インタプリタ初期化の時点で注入すると `__main__` の関数はまだ無い。
    # ここで降りると、その1セッションは録れないまま終わる。
    main = sys.modules.get("__main__")
    if main is not None and getattr(main, "make_crash_log", None) is not None:
        install(main)
        return

    ctx.log("__main__.make_crash_log not found yet; watching for it "
            "(every {:.0f}s, up to {:.0f}s)".format(WATCH_POLL, WATCH_SECONDS))

    def watch():
        deadline = time.monotonic() + WATCH_SECONDS
        while not ctx.superseded():
            module = sys.modules.get("__main__")
            if module is not None and getattr(module, "make_crash_log", None) is not None:
                # 直前でもう一度確かめる。
                # ループの判定から今までの間に新しい boot が始まっていることがあり、
                # その boot の apply() は関数が在る状態で走るので、そちらに任せてよい。
                if ctx.superseded():
                    ctx.log("crash log probe: a newer boot took over; "
                            "leaving the late arm to it")
                    return
                try:
                    install(module)
                    ctx.log("crash log probe: late-armed (make_crash_log appeared)")
                    ctx.refresh_status()
                except Exception:
                    ctx.log_exc("crash log probe: could not arm late")
                return
            if time.monotonic() > deadline:
                ctx.log("crash log probe: gave up waiting for "
                        "__main__.make_crash_log", level="WARN")
                return
            time.sleep(WATCH_POLL)

    threading.Thread(target=watch, name="instantale_crash_log_probe.watch",
                     daemon=True).start()
