# -*- coding: utf-8 -*-
r"""ゲームの更新で MOD が効かなくなったことを、遊んでいる間に見つける（変化の見張り）。

ゲームの更新で MOD が黙って効かなくなる道は、起動時に「対象の名前が見つからない」と分かるもののほかに3つある。
どれも名前は残っているので、起動時の台帳（`patch_registry` の unresolved）には出ない。

  * **包みの外れ** … ゲームが遊んでいる途中で関数を差し替え、被せた包みが無くなる。
    main_026 は「開始する」のたびに LLM の送り口を作り直していた（VERIFICATION.md §3.100）。
    `AUDIT_SECONDS` ごとに、この世代で被せた包み（`patch.placements()`）が今の値の鎖に残っているかを見る。
    2回続けて外れていたら、控えた当て方で付け直す（`drift_heal`。対象ごとに `HEAL_LIMIT` 回まで）。
    1回目で付け直さないのは、ゲームが差し替えの途中（素の関数を入れてから包む、など）のことがあるため
  * **引数の変化・対象の消失** … 包んでいる対象の引数の並びが前の版と変わる（main_026 で8件）、または消える。
    前の版との関数の比べは recon が持つ（`recon_history\current\changes.json`。`recon.build_changes`）。
    ここではそれを、この世代で包んだ対象で絞って出すだけ。
    `*args` で受けていれば実害が無いことも多いので、出すのは recon が版の変化を見つけてから `REPORT_DAYS` 日の間だけ
  * **書き換えの空振り** … ゲームが頼みの文面を変え、MOD の目印が当たらなくなる
    （main_026 で `111_` の手元の規則「戦闘の自由入力」が当たらなくなった）。
    `llm.wrap_outgoing` の書き換えが当たった回数を、MOD と頼みの種類ごと・版ごとに控え（`drift\rewrites.json`）、
    前の版では当たっていたのに今の版で当たらなくなったものを出す

このほか、recon が版の変化を見つけてから `REPORT_DAYS` 日の間は「ゲームが更新された」こと自体を出す（`check_update`）。
中身だけの更新では上の3つのどれにも当たらないことが多く、遊ぶ側に更新が見えないため。
版の見分けには exe のハッシュも入るので、大きさも版の名前も変わらない更新でも出る。

役割の分け方: ゲーム側の事実（どの版か・前の版から関数がどう変わったか）は recon が持ち、ここは
MOD との突き合わせと、遊んでいる間の見張りだけを持つ。版の鍵も `recon.build_key` を使う。
書き換えの数の控えは `out\` の外の `recon_history\drift\` に置く（recon は zip と `current\` 以外に触らない）。
書き換えの見張りが効くのは、この仕組みを入れてから2つ目の版から（前の版の数が要る）。

見つけたものは `modloader.log`（`drift:` の行）と `status.json` の `drift` に出す。GUI はデバッグモードのときだけ警告の欄に数を出す。

切るには `settings/loader.json` を `{"drift_watch": false}` にする（見張りごと）。
付け直しだけ切るなら `{"drift_heal": false}`（次の注入から）。
"""
from __future__ import annotations

import datetime
import json
import os
import sys
import threading
import time

from . import config, log, log_exc

#: `settings/loader.json` の切り替え。無いか真偽値でなければ入（True）。
FLAG = "drift_watch"
HEAL_FLAG = "drift_heal"

#: 包みの外れを見回る間隔（秒）。
AUDIT_SECONDS = 5.0
#: 同じ対象を付け直す上限（1プロセス）。超えたら付け直さず、外れたままを報告する。
HEAL_LIMIT = 5
#: 書き換えの数を控えのファイルへ書く間隔（秒）。
FLUSH_SECONDS = 60.0
#: 控えに残す版の数。
KEEP_BUILDS = 6
#: 引数の変化を出す期間（recon が版の変化を見つけてからの日数）。
REPORT_DAYS = 7

#: 書き換えの空振りと見なす条件。前の版で `REWRITE_MIN_BEFORE` 回以上当たっていて、
#: 前の版の当たりの割合で数えた見込みが `REWRITE_MIN_EXPECTED` 回以上なのに、今の版で1回も当たらない。
REWRITE_MIN_BEFORE = 3
REWRITE_MIN_EXPECTED = 5.0

REWRITES_NAME = "rewrites.json"

# 1プロセスに1つの状態。ローダは boot ごとに読み直されるので sys に置く。
_STATE_ATTR = "__instantale_drift__"


def _state() -> dict:
    found = getattr(sys, _STATE_ATTR, None)
    if not isinstance(found, dict):
        found = vars(sys).setdefault(_STATE_ATTR, {})
    found.setdefault("lock", threading.Lock())
    found.setdefault("lost", {})          # 対象 -> 報告（外れた包み）
    found.setdefault("heals", {})         # 対象 -> 付け直した回数
    found.setdefault("signatures", [])    # 引数が変わった・消えた対象
    found.setdefault("rewrites", {})      # 鍵 -> 報告（書き換えの空振り）
    found.setdefault("counts", {})        # 鍵 -> [呼ばれた回数, 当たった回数]（このプロセス）
    found.setdefault("dirty", False)
    return found


def enabled(runtime_dir, flag=FLAG) -> bool:
    try:
        value = config.load_flags(runtime_dir).get(flag)
    except Exception:
        log_exc("drift: cannot read {}".format(flag))
        value = None
    return value if isinstance(value, bool) else True


# --------------------------------------------------------------------------
# 版と控えの置き場
# --------------------------------------------------------------------------
def build_key(out_dir: str | None = None) -> str | None:
    """今の版の鍵（`recon.build_key`）。取れなければ None（比べない）。"""
    st = _state()
    if "build" not in st:
        try:
            from . import recon
            st["build"] = recon.build_key(snap_dir=recon.snapshot_dir(out_dir) if out_dir else None)
        except Exception:
            log_exc("drift: cannot tell the build")
            st["build"] = None
    return st["build"]


def store_dir(out_dir: str) -> str:
    from . import recon
    return os.path.join(recon.snapshot_dir(out_dir), recon.DRIFT_DIR_NAME)


def _read(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception:
        log_exc("drift: cannot read {}".format(path))
        return {}


def _write(path: str, data: dict) -> None:
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=1)
        os.replace(tmp, path)
    except Exception:
        log_exc("drift: cannot write {}".format(path))


def _builds(data: dict) -> list:
    builds = data.get("builds")
    return builds if isinstance(builds, list) else []


def _entry(data: dict, key: str) -> dict:
    """版 `key` の控え。無ければ末尾に足す（古い版は `KEEP_BUILDS` まで）。"""
    builds = _builds(data)
    for entry in builds:
        if entry.get("build") == key:
            return entry
    entry = {"build": key, "first_seen": datetime.datetime.now().isoformat(timespec="seconds")}
    builds.append(entry)
    del builds[:-KEEP_BUILDS]
    data["builds"] = builds
    return entry


def _previous(data: dict, key: str) -> dict | None:
    """`key` より前に見た、いちばん新しい別の版の控え。"""
    others = [entry for entry in _builds(data) if entry.get("build") != key]
    return others[-1] if others else None


# --------------------------------------------------------------------------
# 引数の変化・対象の消失
# --------------------------------------------------------------------------
def recent_update(out_dir: str) -> dict | None:
    """recon が `REPORT_DAYS` 日のうちに見つけた版の変化（`changes.json`）。無ければ None。"""
    from . import recon
    path = os.path.join(recon.snapshot_dir(out_dir), recon.CURRENT_DIR_NAME, recon.CHANGES_NAME)
    changes = _read(path)
    if not changes:
        return None
    try:
        detected = datetime.datetime.fromisoformat(changes.get("detected_at"))
        if (datetime.datetime.now() - detected).days >= REPORT_DAYS:
            return None
    except Exception:
        pass
    return changes


def check_update(out_dir: str) -> dict | None:
    """ゲームが更新されたことを出す（引数の変化が無くても。中身だけの更新に気づく合図）。"""
    from . import recon
    changes = recent_update(out_dir)
    st = _state()
    if not changes:
        st.pop("update", None)
        return None
    since = changes.get("since_build") or {}
    functions = changes.get("functions") or []
    update = {"since": since.get("app_version") or recon._snapshot_version(str(changes.get("since") or "?")),
              "detected_at": changes.get("detected_at"),
              "identity": changes.get("identity") or [],
              "changed": sum(1 for item in functions if item.get("kind") in ("changed", "removed"))}
    if st.get("update") != update:
        log("drift: the game was updated since {} (found {}; {}; {} function(s) changed or gone)".format(
            update["since"], update["detected_at"], ", ".join(update["identity"]) or "identity unchanged",
            update["changed"]), level="WARN")
    st["update"] = update
    return update


def check_signatures(out_dir: str) -> list:
    """recon が見つけた前の版からの関数の変化のうち、この世代で包んだ対象のものを出す。並びを返す。"""
    from . import patch, patch_registry
    changes = recent_update(out_dir)
    if not changes or not changes.get("functions"):
        return []
    hooked = {placement["target"] for placement in patch.placements()}
    by_target = patch_registry.by_target()
    st = _state()
    reported = {item["target"] for item in st["signatures"]}
    found = []
    for item in changes["functions"]:
        target = item.get("key")
        if item.get("kind") not in ("changed", "removed") or target not in hooked:
            continue
        if target in reported:
            continue
        entry = {"target": target, "kind": item["kind"], "before": item.get("before"),
                 "now": item.get("now"), "mods": by_target.get(target, []),
                 "since": changes.get("since")}
        st["signatures"].append(entry)
        reported.add(target)
        found.append(entry)
        log("drift: {} {} since {}: {} -> {} (wrapped by {})".format(
            target, "changed its arguments" if item["kind"] == "changed" else "is gone",
            entry["since"], entry["before"], entry["now"], ", ".join(entry["mods"]) or "?"),
            level="WARN")
    return found


# --------------------------------------------------------------------------
# 書き換えの空振り
# --------------------------------------------------------------------------
def note_rewrite(label: str, manager, changed: bool) -> None:
    """`llm.wrap_outgoing` が1回の書き換えのたびに呼ぶ。`manager` は頼みの種類（分からなければ None）。"""
    st = _state()
    key = "{}|{}".format(label, manager or "*")
    with st["lock"]:
        row = st["counts"].setdefault(key, [0, 0])
        row[0] += 1
        if changed:
            row[1] += 1
        st["dirty"] = True


def flush_rewrites(out_dir: str) -> list:
    """このプロセスの数を今の版の控えへ足し、空振りを調べる。新しく見つけた空振りの並びを返す。"""
    key = build_key(out_dir)
    st = _state()
    if not key:
        return []
    path = os.path.join(store_dir(out_dir), REWRITES_NAME)
    with st["lock"]:
        counts = {name: list(row) for name, row in st["counts"].items()}
        flushed = st.setdefault("flushed", {})
        data = _read(path)
        entry = _entry(data, key)
        stored = entry.setdefault("counts", {})
        for name, row in counts.items():
            done = flushed.get(name, [0, 0])
            base = stored.get(name, [0, 0])
            stored[name] = [base[0] + row[0] - done[0], base[1] + row[1] - done[1]]
            flushed[name] = row
        before = _previous(data, key)
        _write(path, data)
        st["dirty"] = False
    found = []
    if not before:
        return found
    old = before.get("counts") or {}
    for name, (calls, hits) in sorted(stored.items()):
        if hits or name in st["rewrites"]:
            continue
        old_calls, old_hits = (old.get(name) or [0, 0])[:2]
        if old_hits < REWRITE_MIN_BEFORE or not old_calls:
            continue
        expected = calls * old_hits / float(old_calls)
        if expected < REWRITE_MIN_EXPECTED:
            continue
        label, _, manager = name.partition("|")
        item = {"label": label, "manager": manager, "calls": calls,
                "before": [old_calls, old_hits], "since": before.get("build")}
        st["rewrites"][name] = item
        found.append(item)
        log("drift: {} has not changed a single {} request in {} call(s) on this build; "
            "it changed {} of {} on {} (the game's prompt text may have changed)".format(
                label, manager, calls, old_hits, old_calls, item["since"]), level="WARN")
    return found


# --------------------------------------------------------------------------
# 包みの外れ
# --------------------------------------------------------------------------
def _screen_name() -> str | None:
    try:
        from kivy.app import App
        root = App.get_running_app().root
        return getattr(root, "current", None)
    except Exception:
        return None


_NOT_SEEN = object()


def audit(heal: bool = True) -> list:
    """この世代で被せた包みが今も残っているかを1回見る。新しく報告したものの並びを返す。

    呼ぶ側が boot の錠を持つこと（付け直しは当て方の途中と重ならないようにする）。
    """
    from . import patch
    st = _state()
    seen_lost = st.setdefault("suspect", {})
    found = []
    by_target: dict = {}
    for placement in patch.placements():
        by_target.setdefault(placement["target"], []).append(placement)
    for target, group in by_target.items():
        missing = []
        for placement in group:
            present = patch.layer_present(target, placement["key"])
            if present is False:
                missing.append(placement)
        if not missing:
            seen_lost.pop(target, None)
            continue
        try:
            current = patch.resolve(target)[2]
        except Exception:
            current = None
        # 1回目は控えるだけ。同じ値のまま次の見回りでも外れていたら、差し替えが済んだと見る
        before = seen_lost.get(target, _NOT_SEEN)
        if before is _NOT_SEEN or before is not current:
            seen_lost[target] = current
            continue
        seen_lost.pop(target, None)
        mods = sorted({p["mod"] for p in missing})
        heals = st["heals"].get(target, 0)
        healed = False
        if heal and heals < HEAL_LIMIT and current is not None:
            healed = all(patch.reapply(p) for p in missing)
            st["heals"][target] = heals + 1
        report = st["lost"].setdefault(target, {"target": target, "mods": mods, "times": 0,
                                                "healed": 0, "first": None, "screens": []})
        report["times"] += 1
        report["healed"] += 1 if healed else 0
        report["last"] = datetime.datetime.now().isoformat(timespec="seconds")
        report["first"] = report["first"] or report["last"]
        screen = _screen_name()
        if screen and screen not in report["screens"]:
            report["screens"].append(screen)
        found.append(report)
        log("drift: the game replaced {} and the wrap(s) of {} were gone{} (screen {}){}".format(
            target, ", ".join(mods), "; wrapped it again" if healed else "",
            screen or "?",
            "" if healed or not heal else "; gave up wrapping it again after {} time(s)".format(
                HEAL_LIMIT)), level="WARN")
    return found


# --------------------------------------------------------------------------
# 回す
# --------------------------------------------------------------------------
def report() -> dict:
    """`status.json` の `drift`。"""
    st = _state()
    # 包んでいる MOD は今の台帳から引き直す（見つけたのが起動の途中で、まだ当たっていない MOD があった）
    try:
        from . import patch_registry
        by_target = patch_registry.by_target()
        for item in st["signatures"]:
            item["mods"] = by_target.get(item["target"], item.get("mods") or [])
    except Exception:
        pass
    return {"build": st.get("build"),
            "update": st.get("update"),
            "lost": list(st["lost"].values()),
            "signatures": list(st["signatures"]),
            "rewrites": list(st["rewrites"].values())}


def start(ctx, out_dir: str) -> bool:
    """boot が MOD の適用の後に呼ぶ。引数を比べ、見回りのスレッドを立てる（世代ごとに1本）。"""
    if not enabled(ctx.runtime_dir):
        log("drift: {} is off; nothing watches for game updates".format(FLAG), level="WARN")
        return False
    heal = enabled(ctx.runtime_dir, HEAL_FLAG)
    try:
        check_update(out_dir)
    except Exception:
        log_exc("drift: cannot tell whether the game was updated")
    try:
        check_signatures(out_dir)
    except Exception:
        log_exc("drift: cannot compare the arguments of the wrapped targets")

    def loop():
        from . import _boot_lock, write_status
        from . import llm
        last_flush = time.monotonic()
        while not ctx.superseded() and not _state().get("stopped"):
            time.sleep(AUDIT_SECONDS)
            if ctx.superseded() or _state().get("stopped"):
                return
            changed = False
            if not llm.rebuilding():
                lock = _boot_lock()
                if lock.acquire(blocking=False):
                    try:
                        if not ctx.superseded() and not _state().get("stopped"):
                            changed = bool(audit(heal))
                    except Exception:
                        log_exc("drift: the wrap audit failed")
                    finally:
                        lock.release()
            if time.monotonic() - last_flush >= FLUSH_SECONDS and _state().get("dirty"):
                last_flush = time.monotonic()
                try:
                    changed = bool(flush_rewrites(out_dir)) or changed
                except Exception:
                    log_exc("drift: cannot write the rewrite counts")
            if changed:
                write_status(out_dir)

    _state()["stopped"] = False
    threading.Thread(target=loop, name="drift_watch", daemon=True).start()
    from . import patch
    log("drift: watching {} wrap(s) on build {} (heal {})".format(
        len(patch.placements()), build_key(out_dir), "on" if heal else "off"))
    return True


def stop() -> None:
    """見回りを止める（`unload` が剥がす前に呼ぶ。剥がした包みを付け直さないため）。"""
    _state()["stopped"] = True
