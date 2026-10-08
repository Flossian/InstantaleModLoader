# -*- coding: utf-8 -*-
"""作業スレッドが例外で死んだとき、待機のまま黙って止めない。

ゲームは行動1つを別スレッド（`Thread-N (execute)`）で進め、LLM への送信もさらに別スレッド
（`Thread-N (send_request_on_id)`）に出す。
どちらかが例外で死ぬと、ゲームはスレッドの例外の口（`threading.excepthook` →
`__main__.report_crash`）で記録を書くだけで、画面は「…」が回ったまま操作が返らない
（GAME.md §2.4 / §2.12。記録は `001_crash_recorder` の `live_crashes.log` にしか残らない）。

ここでやること:

  * **送信スレッドが死んだら** … 失敗を本文の欄に出し、待っている `send_request` を起こす。
    待っている側が見ているのは、送信の本体（`send_request_on_id_main_body`）の閉包の
    `response_from_llm`。落ちたスレッドのトレースバックに載る `send_request_on_id` の変数から
    その関数を引き、セルに失敗の印（`FAILED_MARK` を持つ物）を書く。待っている側は印をそのまま返すので、
    `llm_manager` の別名の包みが印を `LLMRequestFailed` に変えて投げる。
    呼んだ側（`execute`）はそこで死ぬので、次の項で待機が終わる。
    `finished_event` は再実行のボタンの時計（`count_request_wait`）が見る旗で、待ちの輪は見ていない。
    立てるとボタンが出なくなるだけなので触らない
  * **待機を持つスレッドが死んだら** … 何で止まったか（例外と、経由した MOD）を本文の欄に出し、
    ゲーム自身の待機の終わり（`scripts.functions.finish_button_load`）を呼んで選択肢を返す。
    状態は途中まで書き換わったままになる。それでも理由が見えて操作が返るほうを採る
  * **それ以外のスレッドが死んだら**（画像の生成など）… 本文の欄に出すだけ
  * **4xx の送り直しを早めに打ち切る** … ゲームは送信を `backoff` で包み、諦める条件を
    送信モジュールの `should_give_up(e)` に聞く。4xx（408 / 409 / 429 を除く）も約30回送り直していた
    （VERIFICATION.md §3.88）。同じ送信スレッドで `CLIENT_ERROR_TRIES` 回目の 4xx で諦めさせる

生きているスレッドのフレームからは変数が読めない（Nuitka。TECH.md §6.3）が、関数の
`__closure__` のセル（`compiled_cell`）は読み書きできる（VERIFICATION.md §3.92）。
関数はトレースバックのフレームの変数から引く。

切るには `settings/loader.json` を `{"stall_guard": false}` にする（次の注入から）。
"""
from __future__ import annotations

import re
import sys
import threading
import time

from . import config, log, log_exc, ui
from . import llm

#: `settings/loader.json` の切り替え。無いか真偽値でなければ入（True）。
FLAG = "stall_guard"

REPORT_TARGET = "__main__:report_crash"
#: スレッドの死の記録の題（`report_crash` の第4引数）の書き出し。
THREAD_TITLE = "THREAD CRASH"

#: 送信スレッドの target の名前（スレッド名の括弧の中）。
SEND_THREADS = ("send_request_on_id", "send_request_with_no_structure_on_id")
#: 死ぬと待機が残るスレッド。行動1つ（`execute`）と、会話の直前の人物の詳細生成。
WAIT_THREADS = ("execute", "generate_npc_detail_and_ready")

#: 待機の終わり。ゲーム自身が作業スレッドの終わりで呼ぶ（GAME.md §2.4）。
FINISH_MODULE = "scripts.functions"
FINISH_NAME = "finish_button_load"

#: 同じ送信スレッドで、この回数目の 4xx で諦める。
CLIENT_ERROR_TRIES = 3
#: 4xx でも送り直す値（時間切れ・衝突・混雑）。
RETRYABLE_CLIENT_ERRORS = (408, 409, 429)

#: 同じ文を続けて出さない間隔（秒）。同じ場所で続けて死ぬと本文が埋まる。
SAME_TEXT_SECONDS = 5.0

#: `report_crash` が現れるのを待つ間隔と上限（秒）。`001_` と同じ。
WATCH_POLL = 2.0
WATCH_SECONDS = 600.0

#: 本文の欄に出す文の書き出し。ゲームの文と見分けるため。
PREFIX = "［MOD ローダ］"

#: `LLMRequestFailed` の印。注入し直すとクラスが別物になるので、isinstance ではなくこれで見る。
FAILED_MARK = "_instantale_llm_request_failed"

_STATE_ATTR = "_instantale_stalls"
_MOD_PATH = re.compile(r"[\\/](?:mods|local)[\\/](\d{3}_[^\\/]+)[\\/]")
_THREAD_TARGET = re.compile(r"\(([^()]+)\)\s*$")
_API_MESSAGE = re.compile(r"""['"]message['"]\s*:\s*(['"])(.+?)\1""")


class LLMRequestFailed(RuntimeError):
    """送信スレッドが死んで、答えが来ないまま `send_request` が返った。"""

    def __init__(self, manager_name, summary):
        super().__init__("LLM request {} failed: {}".format(manager_name, summary))
        self.manager_name = manager_name
        self.summary = summary
        setattr(self, FAILED_MARK, True)


class FailedResponse(object):
    """`response_from_llm` に書く印。待っている `send_request` がこれを返す。"""

    def __init__(self, manager_name, summary):
        self.manager_name = manager_name
        self.summary = summary
        setattr(self, FAILED_MARK, True)

    def __repr__(self):
        return "<FailedResponse {} {}>".format(self.manager_name, self.summary)


def is_failed(value) -> bool:
    return getattr(value, FAILED_MARK, False) is True


def _state():
    """注入をまたいで共有する控え。送信中の呼び出しは前の世代の包みの中にも居る。"""
    state = getattr(sys, _STATE_ATTR, None)
    if not isinstance(state, dict):
        state = vars(sys).setdefault(_STATE_ATTR, {})
    state.setdefault("lock", threading.Lock())
    state.setdefault("inflight", [])
    state.setdefault("shown", {})
    state.setdefault("give_up", {})
    return state


def enabled(runtime_dir) -> bool:
    try:
        value = config.load_flags(runtime_dir).get(FLAG)
    except Exception:
        log_exc("stall guard: cannot read {}".format(FLAG))
        value = None
    return value if isinstance(value, bool) else True


# --------------------------------------------------------------------------
# 例外を読む
# --------------------------------------------------------------------------
def _chain(exc, limit=6):
    seen = []
    while exc is not None and len(seen) < limit and exc not in seen:
        seen.append(exc)
        exc = exc.__cause__ or exc.__context__
    return seen


def status_of(exc):
    """HTTP の状態番号。無ければ None。

    openai / anthropic は `status_code`、httpx は `response.status_code`、
    google.genai は `code`。連鎖も見る（SDK の例外は httpx の例外の処理中に出る）。
    """
    for item in _chain(exc):
        for code in (getattr(item, "status_code", None),
                     getattr(getattr(item, "response", None), "status_code", None),
                     getattr(item, "code", None)):
            if isinstance(code, int) and not isinstance(code, bool) and 100 <= code < 600:
                return code
    return None


def is_client_error(exc) -> bool:
    code = status_of(exc)
    return code is not None and 400 <= code < 500 and code not in RETRYABLE_CLIENT_ERRORS


def summary_of(exc, limit=160) -> str:
    """画面に出す1行。API の誤りは中の `message` を採る（外側は長い辞書の repr）。"""
    if exc is None:
        return "?"
    text = str(exc)
    found = _API_MESSAGE.search(text)
    if found:
        text = found.group(2)
    code = status_of(exc)
    name = type(exc).__name__
    head = "{} {}".format(name, code) if code is not None and str(code) not in text[:12] else name
    text = " ".join(text.split())
    if len(text) > limit:
        text = text[:limit - 1] + "…"
    return "{}: {}".format(head, text) if text else head


def _frames(tb):
    while tb is not None:
        yield tb.tb_frame
        tb = tb.tb_next


def mod_of(tb):
    """トレースバックが通った MOD のうち、いちばん内側のもの。無ければ None。"""
    found = None
    for frame in _frames(tb):
        hit = _MOD_PATH.search(getattr(frame.f_code, "co_filename", "") or "")
        if hit:
            found = hit.group(1)
    return found


def send_frame_locals(tb):
    """送信スレッドのトレースバックから `send_request_on_id` の変数を写す。無ければ None。

    生きたフレームは空だが、トレースバックに載ったフレームは中身がある（`001_` が読んでいる）。
    """
    found = None
    current = None
    for frame in _frames(tb):
        name = frame.f_code.co_name
        if name not in SEND_THREADS and not name.endswith("_on_id_main_body"):
            continue
        try:
            values = dict(frame.f_locals)
        except Exception:
            continue
        if name in SEND_THREADS:
            found = values
        elif "current_request_id" in values:
            current = values.get("current_request_id")
    if found is not None and current is not None:
        found = dict(found, current_request_id=current)
    return found


#: 待っている側が見ているセルの名前（送信の本体の閉包）。
RESPONSE_CELL = "response_from_llm"


def _closure_cells(fn):
    try:
        names = fn.__code__.co_freevars
        cells = fn.__closure__ or ()
    except Exception:
        return {}
    return dict(zip(names, cells))


def response_cell(values):
    """`send_request_on_id` の変数から `response_from_llm` のセルを引く。無ければ None。

    変数の `send_request_on_id_main_body` は `backoff` の包み（閉包に `target`）で、
    素の本体はその `target` のセルの中にある（実機の閉包の並び。VERIFICATION.md §3.92）。
    """
    pending = [value for key, value in values.items()
               if isinstance(key, str) and key.endswith("_on_id_main_body")]
    seen = []
    while pending and len(seen) < 8:
        fn = pending.pop(0)
        if not callable(fn) or any(fn is s for s in seen):
            continue
        seen.append(fn)
        cells = _closure_cells(fn)
        if RESPONSE_CELL in cells:
            return cells[RESPONSE_CELL]
        target = cells.get("target")
        if target is not None:
            try:
                pending.append(target.cell_contents)
            except Exception:
                pass
    return None


def thread_target(name) -> str:
    hit = _THREAD_TARGET.search(name or "")
    return hit.group(1) if hit else ""


# --------------------------------------------------------------------------
# 送信中の呼び出しの控え
# --------------------------------------------------------------------------
def begin_call(manager_name):
    state = _state()
    token = {"manager": manager_name, "thread": threading.current_thread().name,
             "started": time.monotonic(), "failure": None}
    with state["lock"]:
        state["inflight"].append(token)
    return token


def end_call(token):
    state = _state()
    with state["lock"]:
        try:
            state["inflight"].remove(token)
        except ValueError:
            pass
    return token.get("failure")


def claim_call(manager_name, summary):
    """送信が死んだ頼みを待っている呼び出しに失敗を渡す。渡せたら真。

    送信スレッドから呼んだ側のスレッドは引けないので、頼みの名前で突き合わせる。
    同じ名前が2本待っていたら新しいほう（死んだ送信は、まだ失敗を受けていないものの中で後から始まった頼みのはず）。
    """
    state = _state()
    with state["lock"]:
        waiting = [t for t in state["inflight"]
                   if t["manager"] == manager_name and t["failure"] is None]
        if not waiting:
            return False
        max(waiting, key=lambda t: t["started"])["failure"] = summary
    return True


# --------------------------------------------------------------------------
# 画面
# --------------------------------------------------------------------------
def _on_main(fn):
    try:
        from kivy.clock import Clock
        Clock.schedule_once(lambda _dt: fn(), 0)
        return True
    except Exception:
        log_exc("stall guard: cannot schedule on the main thread")
        return False


def _fresh(text) -> bool:
    """同じ文を `SAME_TEXT_SECONDS` 以内に出していなければ真（出した時刻を控える）。"""
    state = _state()
    now = time.monotonic()
    with state["lock"]:
        shown = state["shown"]
        for key in [k for k, at in shown.items() if now - at > SAME_TEXT_SECONDS]:
            del shown[key]
        if text in shown:
            return False
        shown[text] = now
    return True


def show(text, *, end_wait=False):
    """本文の欄に出す。`end_wait` なら、待機中のときだけゲームの待機の終わりも呼ぶ。"""
    # 一文一行で出すので、どの行もゲームの文と見分けられるよう行ごとに印を付ける。
    text = "\n".join(PREFIX + line for line in text.split("\n"))
    fresh = _fresh(text)

    def run():
        app = ui.find_app()
        if app is None:
            log("stall guard: no app; not shown: {}".format(text), level="WARN")
            return
        if fresh:
            try:
                app.add_text(text)
            except Exception:
                log_exc("stall guard: add_text failed")
        if not end_wait:
            return
        if getattr(app, "is_button_enabled", True):
            log("stall guard: the buttons are already enabled; nothing to end")
            return
        finish = getattr(sys.modules.get(FINISH_MODULE), FINISH_NAME, None)
        if not callable(finish):
            log("stall guard: {}.{} not found; the wait stays".format(
                FINISH_MODULE, FINISH_NAME), level="WARN")
            return
        try:
            finish(app)
        except Exception:
            log_exc("stall guard: {} failed".format(FINISH_NAME))
            return
        log("stall guard: ended the wait ({} button(s), enabled={})".format(
            len(getattr(app, "buttons", None) or ()), getattr(app, "is_button_enabled", None)))

    _on_main(run)


# --------------------------------------------------------------------------
# スレッドの死
# --------------------------------------------------------------------------
def on_thread_crash(exc_type, exc_value, exc_traceback):
    """`report_crash` の後で呼ばれる（死んだスレッドの中）。"""
    thread = threading.current_thread().name
    target = thread_target(thread)
    tb = getattr(exc_value, "__traceback__", None) or exc_traceback
    if target in SEND_THREADS:
        on_send_crash(thread, exc_value, tb)
        return
    via = mod_of(tb)
    via_text = "（{} を経由）".format(via) if via else ""
    if getattr(exc_value, FAILED_MARK, False):
        log("stall guard: {} stopped after the failed llm request {}".format(
            thread, getattr(exc_value, "manager_name", "?")))
        show("この行動は中断した。\n選択肢を戻した。", end_wait=target in WAIT_THREADS)
        return
    summary = summary_of(exc_value)
    log("stall guard: {} died: {}{}".format(thread, summary, " via " + via if via else ""),
        level="WARN")
    if target in WAIT_THREADS:
        show("処理が途中で止まった: {}{}。\n選択肢を戻したが、状態が途中のままの可能性がある。".format(
            summary, via_text), end_wait=True)
    else:
        show("裏の処理が止まった: {}{}。".format(summary, via_text))


def on_send_crash(thread, exc, tb):
    values = send_frame_locals(tb) or {}
    manager_name = values.get("manager_name") or "?"
    summary = summary_of(exc)
    log("stall guard: {} for {} died: {}".format(thread, manager_name, summary), level="WARN")
    show("LLM への依頼が失敗した（{}）: {}".format(manager_name, summary))
    request_id = values.get("request_id")
    current = values.get("current_request_id")
    if values.get("abandoned") or (current is not None and request_id is not None
                                   and current != request_id):
        log("stall guard: request {} of {} was already abandoned; not waking".format(
            request_id, manager_name))
        return
    cell = response_cell(values)
    if cell is None:
        log("stall guard: {} not found in the traceback; the wait stays".format(RESPONSE_CELL),
            level="WARN")
        return
    try:
        answered = cell.cell_contents is not None
    except ValueError:
        answered = False                    # まだ一度も書かれていないセル
    except Exception:
        log_exc("stall guard: cannot read {}".format(RESPONSE_CELL))
        return
    if answered:
        log("stall guard: {} already has an answer; not waking".format(manager_name))
        return
    if not claim_call(manager_name, summary):
        # 包みの外の呼び出し（別名がまだ生えていない間など）。起こすと印がそのまま
        # 呼んだ側へ渡る。待たせておく（ゲームの再実行のボタンが残る）。
        log("stall guard: no wrapped call is waiting for {}; not waking".format(manager_name),
            level="WARN")
        return
    try:
        cell.cell_contents = FailedResponse(manager_name, summary)
    except Exception:
        log_exc("stall guard: cannot write {}".format(RESPONSE_CELL))
        return
    log("stall guard: woke the call waiting for {}".format(manager_name))


# --------------------------------------------------------------------------
# 取り付け
# --------------------------------------------------------------------------
def install(ctx):
    """1つの世代に1回。boot が MOD の適用の後に呼ぶ。"""
    if not enabled(ctx.runtime_dir):
        log("stall guard: {} is off; dead worker threads leave the wait as it is".format(FLAG),
            level="WARN")
        return False
    _install_report(ctx)
    _install_send(ctx)
    return True


def _install_report(ctx):
    def arm():
        @ctx.wrap(REPORT_TARGET, safe=True)
        def report_crash(orig, *args, **kwargs):
            try:
                return orig(*args, **kwargs)
            finally:
                try:
                    title = str(_arg(args, kwargs, "title", 3, ""))
                    if title.startswith(THREAD_TITLE):
                        on_thread_crash(_arg(args, kwargs, "exc_type", 0),
                                        _arg(args, kwargs, "exc_value", 1),
                                        _arg(args, kwargs, "exc_traceback", 2))
                except Exception:
                    log_exc("stall guard: failed while handling a thread crash")

    main = sys.modules.get("__main__")
    if main is not None and getattr(main, "report_crash", None) is not None:
        arm()
        return

    # 起動の早い時点で注入すると、まだ無い（`001_` と同じ待ち方）。
    def watch():
        from . import _boot_lock
        from . import patch_registry as _registry
        deadline = time.monotonic() + WATCH_SECONDS
        while not ctx.superseded():
            module = sys.modules.get("__main__")
            if module is not None and getattr(module, "report_crash", None) is not None:
                with _boot_lock():
                    if ctx.superseded():
                        return
                    _registry.begin_mod("(loader)")
                    try:
                        arm()
                    finally:
                        _registry.end_mod()
                log("stall guard: late-armed on {}".format(REPORT_TARGET))
                return
            if time.monotonic() > deadline:
                log("stall guard: gave up waiting for {}".format(REPORT_TARGET), level="WARN")
                return
            time.sleep(WATCH_POLL)

    def guarded():
        try:
            watch()
        except Exception:
            log_exc("stall guard: the watch for {} stopped".format(REPORT_TARGET))

    threading.Thread(target=guarded, name="instantale_stall_guard.watch", daemon=True).start()


def _arg(args, kwargs, name, index, default=None):
    if name in kwargs:
        return kwargs[name]
    return args[index] if len(args) > index else default


def _install_send(ctx):
    def hook(target):
        @ctx.wrap(target, required=False)
        def manager_send(orig, *args, **kwargs):
            manager_name = _arg(args, kwargs, "manager_name", 0, "?")
            _arm_give_up(ctx)
            token = begin_call(manager_name)
            main = threading.current_thread() is threading.main_thread()
            try:
                result = orig(*args, **kwargs)
            except Exception as exc:
                failure = end_call(token)
                # 待っている側は答えを添字で引いてから返すので、印を受けるとその場で
                # `TypeError` になる（実機。VERIFICATION.md §3.92）。失敗を受けた呼び出しなら置き換える。
                if failure is None or main:
                    raise
                raise LLMRequestFailed(manager_name, failure) from exc
            end_call(token)
            if not is_failed(result):
                return result
            # 印は呼んだ側へ渡さない（どこで死ぬか分からなくなる）。
            # メインスレッドで投げるとゲームごと落ちるので、そこでは答えの無い形（None）で返す。
            if main:
                return None
            raise LLMRequestFailed(manager_name, getattr(result, "summary", "?"))

    llm.watch_aliases(ctx, [target for target, _ns in llm.MANAGER_SEND_TARGETS], hook,
                      label="stall guard")


def _arm_give_up(ctx):
    """読み込まれた送信モジュールの `should_give_up` を包む（世代ごとに1回）。

    送信モジュールは最初の頼みまで読み込まれず、どれが読まれるかはプロバイダで決まるので、
    頼みの入口（別名の包み）から当てる。
    """
    state = _state()
    done = state["give_up"]
    pending = [name for name in llm.request_modules()
               if done.get(name) != ctx.generation
               and callable(getattr(sys.modules.get(name), "should_give_up", None))]
    if not pending:
        return
    from . import _boot_lock
    from . import patch_registry as _registry
    with _boot_lock():
        if ctx.superseded():
            return
        for name in pending:
            if done.get(name) == ctx.generation:
                continue
            done[name] = ctx.generation
            _registry.begin_mod("(loader)")
            try:
                _wrap_give_up(ctx, name)
            except Exception:
                log_exc("stall guard: cannot wrap {}:should_give_up".format(name))
            finally:
                _registry.end_mod()


def _wrap_give_up(ctx, module_name):
    counts = threading.local()

    @ctx.wrap("{}:should_give_up".format(module_name), required=False, alias_scan=False,
              safe=True)
    def should_give_up(orig, exc, *args, **kwargs):
        if orig(exc, *args, **kwargs):
            return True
        if not is_client_error(exc):
            return False
        # 送信スレッドは頼みごとに新しく立つので、数はスレッドごと。
        counts.n = getattr(counts, "n", 0) + 1
        if counts.n < CLIENT_ERROR_TRIES:
            return False
        log("stall guard: giving up after {} client error(s): {}".format(
            counts.n, summary_of(exc)))
        return True

    log("stall guard: wrapped {}:should_give_up".format(module_name))
