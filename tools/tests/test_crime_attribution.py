# -*- coding: utf-8 -*-
"""`119_fix_crime_attribution` をゲーム抜きで通す。

    python tools/tests/test_crime_attribution.py

見ているのは3つ。

  書換     … 対象 system 文を facilitator / summarizer と見分けて置換すること。
             元の並びを壊さないこと。二度当てても増えないこと
  戻り値   … マーカーが `other_or_none` のときだけ逮捕と評判低下を消し、
             欠落・player・未知の形では元の結果を保つこと
  経路     … **ローカル（chat）でもクラウド（`llm_manager` の別名）でも**
             書き換わること。v1 は前者だけで、クラウドでは何もしていなかった

経路の口は `instantale_modloader.llm` にあるので、偽の `LlamaCppClient` と
偽の `llm_manager` を立てて `apply(ctx)` をそのまま通す。
"""

import importlib.util
import io
import json
import os
import shutil
import sys
import tempfile
import types


HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
RUNTIME_DIR = os.path.join(ROOT, "runtime")
MODS_DIR = os.path.join(RUNTIME_DIR, "mods")

if RUNTIME_DIR not in sys.path:
    sys.path.insert(0, RUNTIME_DIR)

from instantale_modloader import llm as ml_llm          # noqa: E402


def find_mod(suffix):
    """mod を **番号を除いた名前** で探す（番号は振り直されることがある）。"""
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
    return folder, os.path.join(folder, entry)


MOD_DIR, MOD_PATH = find_mod("_fix_crime_attribution")


def load_mod():
    spec = importlib.util.spec_from_file_location(
        "crime_attribution_fix_test", MOD_PATH,
        submodule_search_locations=[MOD_DIR])
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


M = load_mod()


def check(condition, message):
    if not condition:
        raise AssertionError(message)


# --------------------------------------------------------------------------
# 偽のゲーム側（`tools/tests/test_llm_prompt_replace.py` と同じ形）
# --------------------------------------------------------------------------
class FakeClient(object):
    """`LlamaCppClient` の3つの地点だけを持つ偽物。本物と同じ入れ子。"""

    def __init__(self):
        self.sent = []          # 一番内側に届いた文章

    def chat(self, model, messages, format=None):
        prompt = self._apply_chat_template(model, messages)
        return self._post_with_model_loading_retry(
            "/completion", {"prompt": prompt, "json_schema": {}})

    def _apply_chat_template(self, model, messages, timeout=None):
        return "\n".join(m.get("content") or "" for m in messages)

    def _post_with_model_loading_retry(self, url, payload, timeout=None):
        self.sent.append(payload["prompt"])
        return {"content": "ok"}


_PRISTINE = {name: FakeClient.__dict__[name] for name in
             ("chat", "_apply_chat_template", "_post_with_model_loading_retry")}


def revert_client():
    """`wrap` で差し替えたメソッドを素に戻す（テスト間で層を積まないため）。"""
    for name, func in _PRISTINE.items():
        setattr(FakeClient, name, func)


class FakeManager(object):
    """`llm_manager` の別名と `master_ai_*` の偽物。

    本物はモジュール関数なので **self が無い**。`__module__` は from-import 元
    （＝プロバイダの送信モジュール）になるので、偽物にもそれを付ける。
    """

    BACKEND = "scripts.llm.request_llm_inference_gemini_test_streaming"

    def __init__(self, summarizer_result=None):
        self.sent = []          # send_request に届いた本文（リストのリスト）
        self.result = summarizer_result

        def send_request(manager_name, message, structure,
                         model=None, max_tokens=30000, timeout=None):
            self.sent.append([m.get("content") or "" for m in message])
            return {"content": "ok"}

        def send_request_with_no_structure(manager_name, message,
                                           model=None, max_tokens=30000,
                                           timeout=30):
            self.sent.append([m.get("content") or "" for m in message])
            return "ok"

        send_request.__module__ = self.BACKEND
        send_request_with_no_structure.__module__ = self.BACKEND
        self.send_request = send_request
        self.send_request_with_no_structure = send_request_with_no_structure

        def master_ai_process_summarizer(*args, **kwargs):
            # 本物と同じく、内側で send_request を呼んでから結果を返す。
            self.send_request("m", kwargs.get("message") or args[1], object())
            return self.result
        self.master_ai_process_summarizer = master_ai_process_summarizer


class FakeCtx(object):
    """`apply(ctx)` が使うぶんだけの ctx。"""

    def __init__(self, client, manager, out_dir):
        self.client = client
        self.manager = manager
        self.out_dir = out_dir
        self.mod_dir = os.path.join(out_dir, "mod")
        self.lines = []
        self.errors = []

    def log(self, msg, level="INFO"):
        self.lines.append("{} {}".format(level, msg))

    def log_exc(self, msg):
        import traceback
        self.errors.append(msg + "\n" + traceback.format_exc())

    def out_path(self, *parts):
        path = os.path.join(self.out_dir, *parts)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        return path

    # ログは本物の `ctx.logger` をそのまま借りる。ここを自前で書くと、
    # 検査だけが別のログ処理を通ることになる（`write_json` と同じ理由）。
    _mod = None

    def logger(self, name, *, tag=None, stamp=True, label=None):
        import instantale_modloader as _ml
        return _ml.ModContext.logger(self, name, tag=tag, stamp=stamp,
                                     label=label)

    def wrap(self, target, required=True):
        name = target.partition(":")[2].rsplit(".", 1)[-1]

        # llm_manager 側はモジュール関数なので self を差し込まない。
        if "llm_manager" in target:
            manager = self.manager

            def decorate_manager(fn):
                original = getattr(manager, name, None)
                if original is None:
                    return fn                 # required=False と同じ扱い

                def wrapper(*args, **kwargs):
                    return fn(original, *args, **kwargs)

                setattr(manager, name, wrapper)
                return fn
            return decorate_manager

        def decorate(fn):
            original = getattr(type(self.client), name)

            def wrapper(client_self, *args, **kwargs):
                return fn(original, client_self, *args, **kwargs)

            setattr(type(self.client), name, wrapper)
            return fn
        return decorate

    def resolve(self, target):
        name = target.partition(":")[2].rsplit(".", 1)[-1]
        if "llm_manager" in target:
            return self.manager, name, getattr(self.manager, name, None)
        return type(self.client), name, getattr(type(self.client), name, None)

    def superseded(self):
        return False


def arm(out_dir, manager=None):
    """素の偽物に `apply()` を当てて `(client, manager, ctx)` を返す。"""
    revert_client()
    client = FakeClient()
    manager = manager or FakeManager()
    ctx = FakeCtx(client, manager, out_dir)
    M.apply(ctx)
    check(not ctx.errors, "apply must not raise: {}".format(ctx.errors))
    check(any("installed" in line for line in ctx.lines),
          "self-check must pass: {}".format(ctx.lines))
    return client, manager, ctx


# --------------------------------------------------------------------------
# 書き換え（純粋関数）
# --------------------------------------------------------------------------
def test_prompt_rewrite():
    original = [M.LAW_ANCHOR]
    rewritten, kind, reason = M.rewrite_texts(original)
    check(kind == "summarizer" and reason == "rewritten", "summarizer rewrite")
    check(rewritten is not original, "texts must be copied")
    check(M.INJECT_MARKER in rewritten[0], "inject marker")
    check(original[0] == M.LAW_ANCHOR, "original must remain unchanged")

    again, again_kind, again_reason = M.rewrite_texts(rewritten)
    check(again is rewritten, "idempotent rewrite must preserve the list")
    check(again_kind is None and again_reason == "already_injected", "idempotency")

    facilitator, kind, reason = M.rewrite_texts([M.ARREST_ANCHOR])
    check(kind == "facilitator" and reason == "rewritten", "facilitator rewrite")
    check(M.MARK_PLAYER in facilitator[0], "facilitator marker rule")

    untouched = ["unrelated"]
    result, kind, reason = M.rewrite_texts(untouched)
    check(result is untouched and kind is None and reason == "not_target", "non-target")

    # 目印が両方あるものは知らない形。素通しして理由を残す。
    _result, kind, reason = M.rewrite_texts([M.LAW_ANCHOR, M.ARREST_ANCHOR])
    check(kind is None and reason == "ambiguous_anchor", "ambiguous anchors")

    # 並びの中の1本だけが対象でも当たる（system 文は複数のうちの1つ）。
    many, kind, _reason = M.rewrite_texts(["前置き", M.LAW_ANCHOR, "後書き"])
    check(kind == "summarizer" and many[0] == "前置き" and many[2] == "後書き",
          "only the anchored text must change")


# --------------------------------------------------------------------------
# 戻り値
# --------------------------------------------------------------------------
def test_summarizer_dict():
    result = {
        "summary": M.MARK_OTHER + " 父親がマルタを加害した。",
        "lawfulness_loss": 10,
    }
    returned, reason, previous = M.postprocess_summarizer(result)
    check(returned is result, "dict identity")
    check(reason == "other_loss_zeroed" and previous == 10, "other loss reason")
    check(result["lawfulness_loss"] == 0, "other loss must be zero")
    check(result["summary"] == "父親がマルタを加害した。", "marker must be stripped")

    player = {
        "summary": M.MARK_PLAYER + " リノが店から盗んだ。",
        "lawfulness_loss": 5,
    }
    M.postprocess_summarizer(player)
    check(player["lawfulness_loss"] == 5, "player crime must remain")
    check(player["summary"] == "リノが店から盗んだ。", "player marker strip")

    missing = {"summary": "マーカー無し", "lawfulness_loss": 7}
    _result, reason, _previous = M.postprocess_summarizer(missing)
    check(reason == "marker_missing" and missing["lawfulness_loss"] == 7,
          "missing marker must keep original")


def test_facilitator_dict():
    result = {
        "think": M.MARK_OTHER + " NPCが加害者である。",
        "process": [
            {"type": "npc_say", "statement": "..."},
            {"type": "arrest_player", "charges": "誤判定"},
        ],
    }
    _returned, reason, removed = M.postprocess_facilitator(result)
    check(reason == "other_arrest_removed" and removed == 1, "remove arrest")
    check([item["type"] for item in result["process"]] == ["npc_say"],
          "keep unrelated process")
    check(result["think"] == "NPCが加害者である。", "strip think marker")

    player = {
        "think": M.MARK_PLAYER + " リノが窃盗した。",
        "process": [{"type": "arrest_player"}],
    }
    M.postprocess_facilitator(player)
    check(len(player["process"]) == 1, "real arrest must remain")


def test_object_shape():
    result = types.SimpleNamespace(
        summary=M.MARK_OTHER + " 第三者同士の事件。",
        lawfulness_loss=8,
    )
    _returned, reason, previous = M.postprocess_summarizer(result)
    check(reason == "other_loss_zeroed" and previous == 8, "object reason")
    check(result.lawfulness_loss == 0, "object loss must be writable")
    check(result.summary == "第三者同士の事件。", "object marker strip")


# --------------------------------------------------------------------------
# 経路
# --------------------------------------------------------------------------
def test_local_path(tmp):
    """ローカル（llama.cpp）の chat 経由で書き換わること。"""
    client, _manager, ctx = arm(os.path.join(tmp, "local"))
    client.chat("m", [{"role": "system", "content": M.LAW_ANCHOR},
                      {"role": "user", "content": "何が起きた？"}], {})
    check(len(client.sent) == 1, "one request must reach the client")
    check(M.INJECT_MARKER in client.sent[0], "local prompt must be rewritten")
    check(M.LAW_ANCHOR not in client.sent[0], "the anchor must be replaced")
    # 入れ子の3点で二重に当たらない（当たっても冪等だが、印が効いていること）。
    check(client.sent[0].count(M.INJECT_MARKER) == 1, "one injection per request")
    check(not ctx.errors, ctx.errors)


def test_cloud_path(tmp):
    """クラウド（`llm_manager` の別名）経由で書き換わること。**v2 の修正点**。"""
    _client, manager, ctx = arm(os.path.join(tmp, "cloud"))
    manager.send_request("m", [{"role": "system", "content": M.ARREST_ANCHOR}],
                         object())
    check(len(manager.sent) == 1, "one request must reach the provider")
    check(M.INJECT_MARKER in manager.sent[0][0], "cloud prompt must be rewritten")
    check(M.MARK_PLAYER in manager.sent[0][0], "the marker rule must be sent")

    # structure 無しの別名も同じ。
    manager.send_request_with_no_structure(
        "m", [{"role": "system", "content": M.LAW_ANCHOR}])
    check(M.INJECT_MARKER in manager.sent[1][0], "the _ns alias must rewrite too")

    # message= のキーワード渡しでも当たる（プロバイダによって並びが違う）。
    manager.send_request("m", message=[{"role": "system", "content": M.LAW_ANCHOR}],
                         structure=object())
    check(M.INJECT_MARKER in manager.sent[2][0], "keyword message must rewrite")
    check(not ctx.errors, ctx.errors)

    # ログにプロバイダ名が残る（どの経路で効いたのかを後から読むため）。
    log = io.open(os.path.join(tmp, "cloud", M.LOG_BASENAME),
                  encoding="utf-8").read()
    check("gemini_test_streaming" in log, "the provider name must be logged:\n" + log)


def test_cloud_skipped_when_local(tmp):
    """ローカル実行では `llm_manager` 境界に触らないこと（二重適用を避ける）。"""
    _client, manager, ctx = arm(os.path.join(tmp, "cloud_local"))
    sys.modules[ml_llm.LOCAL_REQUEST_MODULE] = object()   # 印だけ。中身は見ない
    try:
        manager.send_request("m", [{"role": "system", "content": M.LAW_ANCHOR}],
                             object())
    finally:
        del sys.modules[ml_llm.LOCAL_REQUEST_MODULE]
    check(manager.sent == [[M.LAW_ANCHOR]],
          "the manager boundary must be untouched in local runs")
    check(not ctx.errors, ctx.errors)


def test_cloud_round_trip(tmp):
    """クラウドで、置換 → LLM のマーカー → 評判低下の取り消し まで繋がること。"""
    manager = FakeManager(summarizer_result={
        "summary": M.MARK_OTHER + " 父親がマルタを加害した。",
        "lawfulness_loss": 10,
    })
    _client, manager, ctx = arm(os.path.join(tmp, "round"), manager=manager)
    result = manager.master_ai_process_summarizer(
        "m", [{"role": "system", "content": M.LAW_ANCHOR}])
    check(M.INJECT_MARKER in manager.sent[0][0], "prompt must be rewritten")
    check(result["lawfulness_loss"] == 0, "third-party crime must not cost reputation")
    check(result["summary"] == "父親がマルタを加害した。", "marker must be stripped")
    check(not ctx.errors, ctx.errors)


def test_untouched_prompt(tmp):
    """対象でない文章はそのまま送ること（何も足さない）。"""
    client, manager, ctx = arm(os.path.join(tmp, "plain"))
    client.chat("m", [{"role": "user", "content": "こんにちは"}], {})
    check(client.sent == ["こんにちは"], client.sent)
    manager.send_request("m", [{"role": "user", "content": "こんにちは"}], object())
    check(manager.sent == [["こんにちは"]], manager.sent)
    check(not ctx.errors, ctx.errors)


def main():
    tmp = tempfile.mkdtemp(prefix="crime_attribution_test_")
    try:
        tests = (
            test_prompt_rewrite,
            test_summarizer_dict,
            test_facilitator_dict,
            test_object_shape,
        )
        for test in tests:
            test()
            print("ok {}".format(test.__name__))

        route_tests = (
            test_local_path,
            test_cloud_path,
            test_cloud_skipped_when_local,
            test_cloud_round_trip,
            test_untouched_prompt,
        )
        for test in route_tests:
            test(tmp)
            print("ok {}".format(test.__name__))
    finally:
        revert_client()
        shutil.rmtree(tmp, ignore_errors=True)
    print("{} crime attribution tests passed".format(len(tests) + len(route_tests)))


if __name__ == "__main__":
    main()
