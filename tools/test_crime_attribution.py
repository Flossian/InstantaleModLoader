# -*- coding: utf-8 -*-
"""`119_fix_crime_attribution` の純粋関数テスト。ゲーム不要。

    python tools/test_crime_attribution.py
"""

import importlib.util
import io
import json
import os
import types


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODS_DIR = os.path.join(ROOT, "runtime", "mods")


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
    return os.path.join(folder, entry)


MOD_PATH = find_mod("_fix_crime_attribution")


def load_mod():
    spec = importlib.util.spec_from_file_location("crime_attribution_fix_test", MOD_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


M = load_mod()


def check(condition, message):
    if not condition:
        raise AssertionError(message)


def test_prompt_rewrite():
    original = [{"role": "system", "content": M.LAW_ANCHOR}]
    rewritten, kind, reason = M.rewrite_messages(original)
    check(kind == "summarizer" and reason == "rewritten", "summarizer rewrite")
    check(rewritten is not original, "messages must be copied")
    check(rewritten[0] is not original[0], "changed message must be copied")
    check(M.INJECT_MARKER in rewritten[0]["content"], "inject marker")
    check(original[0]["content"] == M.LAW_ANCHOR, "original must remain unchanged")

    again, again_kind, again_reason = M.rewrite_messages(rewritten)
    check(again is rewritten, "idempotent rewrite must preserve list")
    check(again_kind is None and again_reason == "already_injected", "idempotency")

    facilitator, kind, reason = M.rewrite_messages(
        [{"role": "system", "content": M.ARREST_ANCHOR}]
    )
    check(kind == "facilitator" and reason == "rewritten", "facilitator rewrite")
    check(M.MARK_PLAYER in facilitator[0]["content"], "facilitator marker rule")

    untouched = [{"role": "system", "content": "unrelated"}]
    result, kind, reason = M.rewrite_messages(untouched)
    check(result is untouched and kind is None and reason == "not_target", "non-target")


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


def main():
    tests = (
        test_prompt_rewrite,
        test_summarizer_dict,
        test_facilitator_dict,
        test_object_shape,
    )
    for test in tests:
        test()
        print("ok {}".format(test.__name__))
    print("{} crime attribution tests passed".format(len(tests)))


if __name__ == "__main__":
    main()
