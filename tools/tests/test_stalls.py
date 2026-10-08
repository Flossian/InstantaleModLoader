# -*- coding: utf-8 -*-
"""ローダの `stalls`（作業スレッドが死んだときに待機のまま止めない）をゲーム抜きで通す。

    python tools/tests/test_stalls.py

確認するもの:

  例外の読み方 … 状態番号（openai / anthropic の status_code・httpx の response・genai の code・連鎖）、
                  4xx の判定（408 / 409 / 429 は送り直す側）、画面に出す1行、経由した MOD
  送信の死     … トレースバックの `send_request_on_id` の変数から送信の本体の閉包を辿り
                  （`backoff` の包みの `target` の中）、`response_from_llm` のセルに失敗の印を書く。
                  見捨てた頼み・答えが入っている・待っている呼び出しが無いときは書かない
  呼んだ側     … 印が返ったら `LLMRequestFailed`。印でなければそのまま返す。メインスレッドでは投げずに None
  諦め         … ゲームの判定が真なら真。4xx は同じスレッドで3回目に真。5xx は偽のまま
  待機の終わり … 待機を持つスレッドの死で本文に出して `finish_button_load` を呼ぶ。それ以外は出すだけ。
                  ボタンが生きていれば終わりを呼ばない。同じ文は続けて出さない
  切り替え     … `loader.json` の `stall_guard` が false なら取り付けない
"""
import json
import os
import shutil
import sys
import tempfile
import threading
import types

HERE = os.path.dirname(os.path.abspath(__file__))
RUNTIME_DIR = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir, "runtime"))
if RUNTIME_DIR not in sys.path:
    sys.path.insert(0, RUNTIME_DIR)

from instantale_modloader import stalls                 # noqa: E402

failures = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name
          + ((" -- " + str(detail)) if detail and not cond else ""))
    if not cond:
        failures.append(name)


def reset():
    if hasattr(sys, stalls._STATE_ATTR):
        delattr(sys, stalls._STATE_ATTR)


# 画面への出し方はその場で呼ぶ（Clock の代わり）。
shown_texts = []
stalls._on_main = lambda fn: (fn(), True)[1]
stalls.log = lambda *a, **k: None
stalls.log_exc = lambda *a, **k: None


class App(object):
    def __init__(self, enabled=False):
        self.is_button_enabled = enabled
        self.buttons = [1, 2]
        self.texts = []

    def add_text(self, text):
        self.texts.append(text)


finished = []
functions = types.ModuleType(stalls.FINISH_MODULE)


def finish_button_load(app):
    finished.append(app)
    app.is_button_enabled = True


functions.finish_button_load = finish_button_load
sys.modules[stalls.FINISH_MODULE] = functions
APP = {"app": None}
stalls.ui.find_app = lambda: APP["app"]


# --------------------------------------------------------------------------
print("例外の読み方")


class APIStatusError(Exception):
    def __init__(self, message, status_code):
        super().__init__(message)
        self.status_code = status_code


class BadRequestError(APIStatusError):
    pass


class HTTPStatusError(Exception):
    def __init__(self, message, code):
        super().__init__(message)
        self.response = types.SimpleNamespace(status_code=code)


class GenaiError(Exception):
    def __init__(self, message, code):
        super().__init__(message)
        self.code = code


def make_400():
    return BadRequestError(
        "Error code: 400 - {'type': 'error', 'error': {'type': 'invalid_request_error', "
        "'message': 'The compiled grammar is too large, which would cause performance issues.'}}",
        400)


anthropic_400 = make_400()
check("openai / anthropic の status_code", stalls.status_of(anthropic_400) == 400)
check("httpx の response.status_code", stalls.status_of(HTTPStatusError("x", 404)) == 404)
check("genai の code", stalls.status_of(GenaiError("x", 400)) == 400)
try:
    try:
        raise HTTPStatusError("inner", 400)
    except HTTPStatusError:
        raise RuntimeError("outer")
except RuntimeError as exc:
    chained = exc
check("連鎖の中の番号", stalls.status_of(chained) == 400)
check("番号の無い例外は None", stalls.status_of(KeyError("229")) is None)
check("真偽値の code は番号にしない", stalls.status_of(GenaiError("x", True)) is None)
check("400 は 4xx", stalls.is_client_error(anthropic_400))
check("429 は送り直す側", not stalls.is_client_error(APIStatusError("x", 429)))
check("408 は送り直す側", not stalls.is_client_error(APIStatusError("x", 408)))
check("500 は 4xx ではない", not stalls.is_client_error(APIStatusError("x", 500)))
check("番号の無い例外は 4xx ではない", not stalls.is_client_error(ValueError("x")))
line = stalls.summary_of(anthropic_400)
check("API の誤りは中の message を採る",
      line == "BadRequestError 400: The compiled grammar is too large, which would cause "
              "performance issues.", line)
check("KeyError はそのまま", stalls.summary_of(KeyError("229")) == "KeyError: '229'",
      stalls.summary_of(KeyError("229")))
check("長い文は切る", len(stalls.summary_of(ValueError("あ" * 500))) <= 160 + len("ValueError: "))
check("スレッド名の括弧の中", stalls.thread_target("Thread-27 (send_request_on_id)")
      == "send_request_on_id")
check("括弧の無いスレッド名は空", stalls.thread_target("MainThread") == "")


def traceback_through(files_and_names, exc):
    """`[(ファイル名, 関数名, {変数})...]` の順に呼んで、最後で `exc` を投げたトレースバック。"""
    source = []
    for index, (filename, name, values) in enumerate(files_and_names):
        call = ("_next_{}()".format(index + 1) if index + 1 < len(files_and_names)
                else "raise _exc")
        assigns = "; ".join("{} = _values_{}[{!r}]".format(k, index, k) for k in values)
        code = "def {}():\n    {}\n    {}\n".format(name, assigns or "pass", call)
        source.append((filename, name, code, values))
    namespace = {"_exc": exc}
    funcs = []
    for index, (filename, name, code, values) in enumerate(source):
        scope = dict(namespace)
        scope["_values_{}".format(index)] = values
        exec(compile(code, filename, "exec"), scope)
        funcs.append(scope)
    for index in range(len(funcs) - 1):
        funcs[index]["_next_{}".format(index + 1)] = funcs[index + 1][source[index + 1][1]]
    try:
        funcs[0][source[0][1]]()
    except BaseException as caught:
        return caught.__traceback__


tb = traceback_through([
    (r"C:\game\instantale.py", "execute", {}),
    (r"C:\x\runtime\mods\322_battle_bgm\battle_bgm.py", "_mark", {}),
    (r"C:\x\runtime\instantale_modloader\patch.py", "guarded", {}),
    (r"C:\x\local\915_facility_investment\facility_investment.py", "method", {}),
    (r"C:\game\instantale.py", "method", {}),
], KeyError("x"))
check("いちばん内側の MOD", stalls.mod_of(tb) == "915_facility_investment", stalls.mod_of(tb))
tb_plain = traceback_through([(r"C:\game\instantale.py", "execute", {})], KeyError("x"))
check("MOD を通っていなければ None", stalls.mod_of(tb_plain) is None)


# --------------------------------------------------------------------------
print("送信の死")


def make_body(response=None, current=7):
    """ゲームの送信の本体と同じ閉包の並び（`current_request_id` / `message` / `response_from_llm`）。"""
    current_request_id = current
    message = []
    response_from_llm = response

    def send_request_on_id_main_body():
        return current_request_id, message, response_from_llm
    return send_request_on_id_main_body


def backoff_wrap(target):
    """`backoff` の包みと同じ形（閉包の `target` に本体）。"""
    exception = Exception

    def retry():
        return exception, target()
    retry.__name__ = "send_request_on_id_main_body"
    return retry


def response_of(body):
    return stalls._closure_cells(body)[stalls.RESPONSE_CELL].cell_contents


def send_traceback(body, *, request_id=7, current=7, abandoned=False, manager="random_quest"):
    return traceback_through([
        (r"C:\game\scripts\llm\request_llm_inference_claude.py", "send_request_on_id",
         {"manager_name": manager, "request_id": request_id, "abandoned": abandoned,
          "send_request_on_id_main_body": backoff_wrap(body)}),
        (r"C:\game\backoff\_sync.py", "retry", {}),
        (r"C:\game\scripts\llm\request_llm_inference_claude.py", "send_request_on_id_main_body",
         {"manager_name": manager, "request_id": request_id, "current_request_id": current}),
    ], make_400())    # 同じ例外を投げ直すと、前のトレースバックの後ろに継ぎ足される


reset()
body = make_body()
values = stalls.send_frame_locals(send_traceback(body))
check("変数を読む", values and values["manager_name"] == "random_quest"
      and values["current_request_id"] == 7, values)
cell = stalls.response_cell(values)
check("backoff の包みの target から本体のセルを引く",
      cell is not None and cell is stalls._closure_cells(body)[stalls.RESPONSE_CELL])
check("本体が無ければ None", stalls.response_cell({"manager_name": "x"}) is None)
check("本体を直に持っていても引く",
      stalls.response_cell({"send_request_on_id_main_body": body}) is cell)

reset()
APP["app"] = App()
token = stalls.begin_call("random_quest")
body = make_body()
stalls.on_send_crash("Thread-27 (send_request_on_id)", anthropic_400, send_traceback(body))
written = response_of(body)
check("response_from_llm に失敗の印を書く", stalls.is_failed(written)
      and written.manager_name == "random_quest" and "grammar" in written.summary, written)
check("待っている呼び出しに失敗を渡す", token["failure"] and "grammar" in token["failure"],
      token["failure"])
check("本文に出す", APP["app"].texts and "LLM への依頼が失敗した（random_quest）"
      in APP["app"].texts[-1], APP["app"].texts)
check("送信の死だけでは待機を終えない", not finished)
stalls.end_call(token)

reset()
APP["app"] = App()
token = stalls.begin_call("random_quest")
body = make_body()
stalls.on_send_crash("t", anthropic_400, send_traceback(body, abandoned=True))
check("見捨てた頼みでは書かない", response_of(body) is None and token["failure"] is None)
stalls.on_send_crash("t", anthropic_400, send_traceback(body, request_id=6, current=7))
check("番号が今の頼みでなければ書かない", response_of(body) is None and token["failure"] is None)
answered = make_body(response={"text": "ok"})
stalls.on_send_crash("t", anthropic_400, send_traceback(answered))
check("答えが入っていれば書かない", response_of(answered) == {"text": "ok"}
      and token["failure"] is None)
stalls.end_call(token)
stalls.on_send_crash("t", anthropic_400, send_traceback(body))
check("待っている呼び出しが無ければ書かない", response_of(body) is None)

reset()
old = stalls.begin_call("random_quest")
new = stalls.begin_call("random_quest")
other = stalls.begin_call("quest_referee")
check("同じ名前が2本なら新しいほう", stalls.claim_call("random_quest", "x")
      and new["failure"] == "x" and old["failure"] is None and other["failure"] is None)
check("次の死は残りのほう", stalls.claim_call("random_quest", "y") and old["failure"] == "y")
check("みな受けた後は渡せない", not stalls.claim_call("random_quest", "z"))


# --------------------------------------------------------------------------
print("呼んだ側と諦め")


class Ctx(object):
    generation = "gen"
    runtime_dir = ""

    def __init__(self):
        self.wrapped = {}

    def wrap(self, target, **kw):
        def decorator(func):
            self.wrapped[target] = func
            return func
        return decorator

    def superseded(self):
        return False


ctx = Ctx()
stalls.llm.watch_aliases = lambda ctx, targets, install, **kw: [install(t) for t in targets]
stalls._arm_give_up = lambda ctx: None
stalls._install_send(ctx)
send = ctx.wrapped["scripts.llm.llm_manager:send_request"]


def run_in_thread(fn):
    box = {}

    def body():
        try:
            box["result"] = fn()
        except BaseException as exc:
            box["raised"] = exc
    thread = threading.Thread(target=body, name="Thread-9 (execute)")
    thread.start()
    thread.join()
    return box


reset()


def failing_orig(name, message, structure=None):
    return stalls.FailedResponse(name, "BadRequestError 400: too large")


box = run_in_thread(lambda: send(failing_orig, "random_quest", [], None))
raised = box.get("raised")
check("失敗の印が返ったら LLMRequestFailed",
      raised is not None and getattr(raised, stalls.FAILED_MARK, False)
      and raised.manager_name == "random_quest" and "too large" in raised.summary, box)
check("控えが残らない", not stalls._state()["inflight"])


def answered_orig(name, message, structure=None):
    return None


box = run_in_thread(lambda: send(answered_orig, "random_quest", [], None))
check("印でなければ None もそのまま返す", "raised" not in box and box.get("result") is None, box)


def subscripting_orig(name, message, structure=None):
    """ゲームの send_request と同じく、返す前に答えを添字で引く。"""
    stalls.claim_call(name, "NotFoundError 404: model")
    response = stalls.FailedResponse(name, "NotFoundError 404: model")
    return response["text"]


box = run_in_thread(lambda: send(subscripting_orig, "master_ai", [], None))
raised = box.get("raised")
check("失敗を受けた呼び出しが例外で返ったら LLMRequestFailed",
      raised is not None and getattr(raised, stalls.FAILED_MARK, False)
      and "404" in raised.summary and isinstance(raised.__cause__, TypeError), box)


def broken_orig(name, message, structure=None):
    raise KeyError("weapon")


box = run_in_thread(lambda: send(broken_orig, "master_ai", [], None))
check("失敗を受けていない呼び出しの例外はそのまま", isinstance(box.get("raised"), KeyError), box)
check("例外の後も控えが残らない", not stalls._state()["inflight"])
check("失敗の無い呼び出しは素通し",
      run_in_thread(lambda: send(lambda *a: "text", "talk", []))["result"] == "text")
check("メインスレッドでは投げずに None", send(failing_orig, "random_quest", [], None) is None)

reset()
module = types.ModuleType("scripts.llm.request_llm_inference_fake")
stalls._wrap_give_up(ctx, module.__name__)
give_up = ctx.wrapped[module.__name__ + ":should_give_up"]
game_says = {"value": False}


def orig_give_up(exc):
    return game_says["value"]


game_says["value"] = True
check("ゲームの判定が真なら真", give_up(orig_give_up, ValueError("x")))
game_says["value"] = False
check("4xx の1回目は送り直す", not give_up(orig_give_up, anthropic_400))
check("4xx の2回目も送り直す", not give_up(orig_give_up, anthropic_400))
check("4xx の3回目で諦める", give_up(orig_give_up, anthropic_400))
check("5xx は送り直す", not give_up(orig_give_up, APIStatusError("x", 503)))
check("番号の無い例外はゲームの判定のまま", not give_up(orig_give_up, KeyError("x")))
box = run_in_thread(lambda: give_up(orig_give_up, anthropic_400))
check("数はスレッドごと（新しい送信は1回目から）", box.get("result") is False, box)


# --------------------------------------------------------------------------
print("待機の終わり")


def crash_in(thread_name, exc, tb=None):
    box = {}

    def body():
        stalls.on_thread_crash(type(exc), exc, tb)
    thread = threading.Thread(target=body, name=thread_name)
    thread.start()
    thread.join()
    return box


reset()
finished[:] = []
APP["app"] = App(enabled=False)
crash_in("Thread-3 (execute)", KeyError("229"), tb)
texts = APP["app"].texts
check("待機を持つスレッドの死を本文に出す", texts and "処理が途中で止まった: KeyError: '229'"
      in texts[-1] and "915_facility_investment" in texts[-1], texts)
check("どの行にも書き出しの印", texts and len(texts[-1].split("\n")) == 2
      and all(line.startswith(stalls.PREFIX) for line in texts[-1].split("\n")), texts)
check("finish_button_load を呼ぶ", finished == [APP["app"]])

finished[:] = []
APP["app"] = App(enabled=False)
crash_in("Thread-3 (execute)", KeyError("229"), tb)
check("同じ文は続けて出さない", not APP["app"].texts, APP["app"].texts)
check("出さなくても待機は終える", finished == [APP["app"]])

reset()
finished[:] = []
APP["app"] = App(enabled=True)
crash_in("Thread-4 (execute)", KeyError("21"))
check("ボタンが生きていれば終わりを呼ばない", not finished and APP["app"].texts)

reset()
finished[:] = []
APP["app"] = App(enabled=False)
crash_in("Thread-5 (generate_images)", TypeError("cannot unpack"))
check("待機を持たないスレッドは出すだけ",
      not finished and APP["app"].texts and "裏の処理が止まった" in APP["app"].texts[-1],
      APP["app"].texts)

reset()
finished[:] = []
APP["app"] = App(enabled=False)
crash_in("Thread-6 (execute)", stalls.LLMRequestFailed("random_quest", "x"))
check("頼みの失敗の続きは中断として出して終える",
      finished == [APP["app"]] and "この行動は中断した" in APP["app"].texts[-1], APP["app"].texts)

reset()
finished[:] = []
APP["app"] = App(enabled=False)
body = make_body()
stalls.begin_call("random_quest")
crash_in("Thread-7 (send_request_on_id)", anthropic_400, send_traceback(body))
check("送信スレッドの死は送信の扱いへ", stalls.is_failed(response_of(body)) and not finished)


# --------------------------------------------------------------------------
print("切り替え")
root = tempfile.mkdtemp()
try:
    runtime = os.path.join(root, "runtime")
    os.makedirs(os.path.join(root, "settings"))
    check("loader.json が無ければ入", stalls.enabled(runtime))
    with open(os.path.join(root, "settings", "loader.json"), "w", encoding="utf-8") as fh:
        json.dump({stalls.FLAG: False}, fh)
    check("false なら切", not stalls.enabled(runtime))
    off = Ctx()
    off.runtime_dir = runtime
    check("切なら取り付けない", stalls.install(off) is False and not off.wrapped)
    with open(os.path.join(root, "settings", "loader.json"), "w", encoding="utf-8") as fh:
        json.dump({stalls.FLAG: "no"}, fh)
    check("真偽値でなければ入", stalls.enabled(runtime))
finally:
    shutil.rmtree(root, ignore_errors=True)

print()
if failures:
    print("{} failure(s)".format(len(failures)))
    sys.exit(1)
print("all passed")
