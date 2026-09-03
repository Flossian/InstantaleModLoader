# -*- coding: utf-8 -*-
r"""本体のクラッシュ記録が、どの呼び出しで落ちるのかを録る。

##### 何が起きているか

スレッドが落ちたとき、本体の記録処理が記録を書く前に自分で落ちる。

    File "instantale.py", line 288, in report_crash
    File "instantale.py", line 200, in make_crash_log
    AttributeError: module 'datetime' has no attribute 'now'

`make_crash_log` は文字列を組んで返すだけの関数で、その後段が
`crash_log.txt` への書き込みと送信を持っている。

    report_crash
      ├ make_crash_log(...) -> str          ここで落ちる。以降は走らない
      ├ write_crash_log_to_file(crash_log)  CRASH_LOG_PATH
      ├ should_skip_crash_log_server_send(...)
      └ send_crash_log_to_server(crash_log) CRASH_REPORT_URL

つまり落ちている間、手元に記録は残らず、送信も走らない。
拾えているのは `001_crash_recorder` の `out\live_crashes.log` だけ。

##### なぜ「常に壊れている」とは言えないのか

`__main__.datetime` はモジュール束縛で、`datetime.now()` は本来通らない。
ところが recon のスナップショット21件（main_023 から main_025 まで）で
ずっとモジュール束縛のまま、本体の `crash_log.txt` には 014 の記録が10件
正常に書けている。

したがって取り違えているのは `make_crash_log` の中の**常には通らない枝**で、
`instantale.py:200` はそこにある。どの枝かは分からない。
ゲームは Nuitka でコンパイル済みなので逆アセンブルで読むことができない。

手元の記録を突き合わせると、スレッドクラッシュ6件のうち
`001` が控えを残していたのは5件で、本体の `crash_log.txt` はその5件とも空。
残る1件は逆に `001` が拾っておらず、本体だけが書けていた
（MAIN のクラッシュ9件はどれも本体が書けている）。

`001` が経路に居た回はどれも本体側が書けておらず、本体が唯一書けた
スレッドクラッシュは `001` が拾っていない回だった。
`001` は控えを取ってから `orig` をそのまま呼ぶだけなので因果は見えないが、
母数が6件では偶然とも言い切れない。

##### 何を録るか

`make_crash_log` を包んで、1回の呼び出しごとに次を控える。

  * 呼ばれたスレッドと `title`（型と値。本体が何を渡しているか）
  * 例外の種類と、`__cause__` / `__context__` を辿った連鎖の数
  * その瞬間の `__main__.datetime` の正体
  * 呼び出し元の経路と、そこに MOD の枠が挟まっているか
  * 組み上がった文字列の頭（通った枝の見分けが付く）

落ちた場合はさらに、`datetime` を差し替えて**もう一度だけ**呼び、
記録が出来上がるのかを控える。
`make_crash_log` は文字列を組んで返すだけで、書き込みも送信も後段なので、
ここでやり直しても二重には残らない。

##### ゲームは変えない

やり直しの結果は**捨てて、元の例外をそのまま投げ直す**。
本体から見た挙動は今と1ミリも変わらない
（`crash_log.txt` は書かれないままで、送信も走らないまま）。
差し替えも `finally` で必ず戻す。

直す側（`131_` の想定）が要る答えは「差し替えれば記録が出来るのか」だけで、
それはやり直しの成否で付く。

##### 引き金

待たなくてよい。エリア8ノード32の「なんでも屋「よろず質店」」で
「売買する」を選ぶと、遊んでいる最中に生まれた施設が `world_dict` に
無いために `KeyError` で必ず落ちる（GAME.md §2.23 の
`world_dict` と `save_data_dict` の食い違い）。
この店はスレッドクラッシュを100%再現するので、母数はその場で貯まる。
"""

import datetime as _datetime
import sys
import threading
import time
import traceback

from instantale_modloader import frames

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


def _arg(args, kwargs, name, index):
    """引数を1つ拾う。キーワードを先に見て、無ければ位置で拾う。

    位置は版で動きうるので、名前で当たるならそちらを採る。
    """
    if name in kwargs:
        return kwargs[name]
    if len(args) > index:
        return args[index]
    return None


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


def _route(skip_innermost=1):
    """呼び出し元の経路を1行にする。MOD の枠には印を付ける。

    戻り値は (経路の文字列, MOD の枠が挟まっているか)。
    """
    try:
        stack = traceback.extract_stack()
    except Exception:
        return "<unavailable>", False
    # 一番内側はこの関数自身とその呼び出し元（probe のラッパ）なので落とす。
    stack = stack[:-(skip_innermost + 1)] if skip_innermost + 1 <= len(stack) else []
    stack = stack[-ROUTE_DEPTH:]
    parts = []
    ours = False
    for entry in stack:
        name = entry.filename.rsplit("\\", 1)[-1].rsplit("/", 1)[-1]
        mark = ""
        if frames.is_ours(entry.filename):
            ours = True
            mark = "[mod]"
        parts.append("{}:{} {}{}".format(name, entry.lineno, entry.name, mark))
    return " > ".join(parts), ours


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

        def trial(orig, args, kwargs):
            """`datetime` を差し替えて、もう一度だけ組ませてみる。

            結果は返すだけで、呼び出し側は捨てる（ゲームには渡さない）。
            """
            with lock:
                real = getattr(main, "datetime", _datetime)
                setattr(main, "datetime", _SHIM)
                try:
                    return True, orig(*args, **kwargs)
                except Exception as exc:
                    return False, exc
                finally:
                    # 差し替えたままにしない。
                    # ここで戻らないと、以降の `datetime` 参照が全部この shim を通る。
                    setattr(main, "datetime", real)

        @ctx.wrap("__main__:make_crash_log", required=False)
        def make_crash_log(orig, *args, **kwargs):
            # 記録の失敗でクラッシュ処理を巻き込まない。
            # 控えは全部 try で囲み、失敗してもログに残すだけにする。
            try:
                exc_value = _arg(args, kwargs, "exc_value", 1)
                title = _arg(args, kwargs, "title", 3)
                route, ours = _route()
                state["calls"] += 1
                write("--- make_crash_log #{} ---".format(state["calls"]))
                write("  thread   = {}".format(threading.current_thread().name))
                write("  title    = {} {}".format(
                    type(title).__name__, frames.short(repr(title), SNIP)))
                write("  exc      = {} chain={}".format(
                    getattr(_arg(args, kwargs, "exc_type", 0), "__name__", "?"),
                    _chain_length(exc_value)))
                bound = getattr(main, "datetime", None)
                loaded = sys.modules.get("datetime")
                write("  datetime = {}".format(frames.short(repr(bound), SNIP)))
                # `__main__` が持っているものと、いま import できるものが
                # 同じ実体かどうか。違っていれば、束縛が誰かに差し替わっている。
                write("  datetime is sys.modules['datetime'] = {} | has now = {}".format(
                    bound is loaded, "now" in dir(bound) if bound is not None else "?"))
                write("  boot     = {}".format(getattr(ctx, "generation", "?")))
                write("  mods in path = {}".format("yes" if ours else "no"))
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
                    ok, outcome = trial(orig, args, kwargs)
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
        # いまは `make_crash_log` が落ちるので、どれも走らないはず。
        # 走ったらそれ自体が答えになる。

        @ctx.wrap("__main__:write_crash_log_to_file", required=False)
        def write_crash_log_to_file(orig, *args, **kwargs):
            try:
                text = _arg(args, kwargs, "crash_log", 0)
                write("  -> write_crash_log_to_file: {} chars".format(
                    len(text) if isinstance(text, str) else -1))
            except Exception:
                ctx.log_exc("crash log probe: recording the file write failed")
            return orig(*args, **kwargs)

        @ctx.wrap("__main__:should_skip_crash_log_server_send", required=False)
        def should_skip_crash_log_server_send(orig, *args, **kwargs):
            verdict = orig(*args, **kwargs)
            try:
                write("  -> should_skip_crash_log_server_send = {!r}".format(verdict))
            except Exception:
                ctx.log_exc("crash log probe: recording the send verdict failed")
            return verdict

        @ctx.wrap("__main__:send_crash_log_to_server", required=False)
        def send_crash_log_to_server(orig, *args, **kwargs):
            # ここに来たことだけを控えて、そのまま下へ通す。
            # 送るか止めるかを決めるのはこの probe ではない
            # （`001_crash_recorder` が注入中は止める）。
            try:
                text = _arg(args, kwargs, "crash_log", 0)
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
