# -*- coding: utf-8 -*-
r"""計測: 世界の一括生成（main_026）の仕事ごとの時間。ゲームは変えない。

##### 何を決めるための計測か

一括生成を速くする手立てを選ぶ下調べ（GAME.md §2.27「一括生成」）。
本体は `plan_tasks` が並べた仕事を1件ずつ順に流す。
1エリアは約50秒かかり、検証ワールド2 では残りが 374〜420 件あった。
残りの内訳と、どの種類が時間を食っているか、その時間が GPU（絵）か LLM の待ちかで、
次の2つのどちらを選ぶかが決まる。

- 依存の無い仕事を裏で先に作る（LLM の待ちが大きいなら効く。絵は GPU が1つなので1列のまま）
- 絵を一括生成から外す（絵が大きいなら効く）

| 問い | 見るところ |
|---|---|
| 仕事の内訳 | `plan` 行（始めの1回は全部、後は種類の件数が増えたときだけ .log に出す） |
| 種類ごとの1件の時間 | `task` 行と、走り終えたときの `summary` 行の表 |
| その時間のうち絵（GPU）・CPU・待ち（主に LLM） | `task` 行の `image` / `cpu` / `wait` |
| 仕事と仕事の間の重さ（並べ直し・保存） | `summary` 行の `between` |
| 本当に1件ずつか | `task` 行の `active`（同時に走っていた仕事の数） |
| 残りを流すと何時間か | `summary` 行の `estimate`（画面の残りの件数 × この実行の1件の平均。粗い） |

`plan_tasks` の並びは「残り」ではない。背景・人物などは済んだものを自分で外すが、
モンスターの絵は描き終えても並びに残り、本体がその後で外す（2026-10-15、描き終えた 107 件が
111 件のまま並び、再開すると 0.9 秒で終わった）。残りの件数は画面へ渡る値
（`WorldBulkScreen.update_progress` の `state`）から採る。

仕掛ける所

    __main__:InstantaleApp.run_world_bulk_generation       1回の実行（開始／再開から終わり・中断まで）
    scripts.world_bulk_generation:plan_tasks               仕事の並び
    scripts.world_bulk_generation:atomic_world_write       仕事ごとの保存
    scripts.world_bulk_backend:GameGenerationBackend.generate   仕事1件
    scripts.hud.hud_world_bulk:WorldBulkScreen.update_progress  画面の「完了／残り／失敗」
    image_generation.<方式>.stable_diffusion_manager:generate_image_anime など   絵1枚（GPU）

一括生成のモジュールは「開始／再開」を押した後に読み込まれ、ローダはそれを待って MOD を当て直す。
`BulkGenerator.run` はその前に走り出しているので、実行の境界には起動時から在る
`run_world_bulk_generation` を使う。当て直しで `apply` が作り直されても、走っている実行の集計は
`sys` の `STORE` に置いて引き継ぐ（前の `apply` の包みが終わりのまとめを書く）。

時間の分け方（仕事1件ごと）

    image   その仕事の間に絵を描いていた壁時計の秒（描く関数の外側の1回だけ数える）
    cpu     仕事を走らせたスレッドの CPU 時間から、そのスレッドで絵を描いていた分を引いたもの
    wait    全体 − image − cpu。LLM の応答待ちがほとんど（送信は別スレッドに降りることがあるので、
            その CPU もここに入る）

LLM の送り口は包まない。`llm.wrap_outgoing` で数えると drift の書き換えの集計に
「当たらなかった」として混ざるため。

    out\world_bulk.log     読む用
    out\world_bulk.jsonl   後から数える用

一括生成の外（遊んでいる間）は、絵の包みが「走っている仕事が無い」を見て何もしない。
読み取りだけ。引数は受け取ったまま `orig` へ渡す。乱数・state\・セーブ・世界のファイルを動かさない。
"""
import collections
import datetime
import io
import json
import os
import sys
import threading
import time

from instantale_modloader import frames, imagegen, saves

LOG_BASENAME = "world_bulk.log"
RECORD_BASENAME = "world_bulk.jsonl"

RUN_TARGET = "__main__:InstantaleApp.run_world_bulk_generation"
PLAN_TARGET = "scripts.world_bulk_generation:plan_tasks"
COMMIT_TARGET = "scripts.world_bulk_generation:atomic_world_write"
TASK_TARGET = "scripts.world_bulk_backend:GameGenerationBackend.generate"
PROGRESS_TARGET = "scripts.hud.hud_world_bulk:WorldBulkScreen.update_progress"

#: 画面へ渡る `state` のうち控える鍵。
PROGRESS_KEYS = ("status", "completed", "remaining", "failed")

#: 絵を描く層（`230_probe_image_generation` の manager と同じ関数）。方式は `imagegen` が決める。
IMAGE_MODULE_FORMAT = "image_generation.{}.stable_diffusion_manager"
IMAGE_FUNCS = ("generate_image_anime", "image_to_image_anime", "generate_image_real_lcm")

#: `task` 行に添える名前の長さ。
LABEL_CHARS = 60

#: 集計の置き場（`sys` の属性名）。当て直しをまたいで同じ物を使う。
STORE = "__instantale_probe_world_bulk__"


def _store():
    store = getattr(sys, STORE, None)
    if not isinstance(store, dict):
        store = {}
        setattr(sys, STORE, store)
    store.setdefault("lock", threading.Lock())
    #: 今の実行。走っていなければ None。
    store.setdefault("state", {"run": None, "seq": 0})
    #: 走っている仕事。`{番号: 行}`。絵の時間はここに居る全員に足す（ふつうは1件）。
    store.setdefault("active", {})
    store.setdefault("marks", threading.local())
    return store


def _kind_of(task):
    kind = frames.attr(task, "kind", None)
    return str(kind) if kind is not None else "?"


def _count_kinds(tasks):
    counts = collections.Counter()
    try:
        for task in tasks:
            counts[_kind_of(task)] += 1
    except TypeError:
        return None
    return dict(sorted(counts.items()))


def _seconds(value):
    return round(value, 2)


def apply(ctx):
    write = ctx.logger(LOG_BASENAME)
    record = ctx.jsonl(RECORD_BASENAME)
    store = _store()
    lock, state, active, marks = store["lock"], store["state"], store["active"], store["marks"]

    def now():
        return datetime.datetime.now().isoformat(timespec="milliseconds")

    def emit(kind, line, **fields):
        row = {"at": now(), "kind": kind, "thread": threading.current_thread().name}
        row.update(fields)
        record(row)
        write("{} {}".format(kind, line))

    # ------------------------------------------------------------ 1回の実行
    def runtime_name():
        """`config.json` の LLM の設定。一括生成を始めた時点では送信モジュールがまだ無いので、
        読み込まれたモジュールでなく設定で見る（クラウドのモデルは `136_` が差し替えることがある）。"""
        try:
            with io.open(os.path.join(saves.data_dir(), "config.json"), encoding="utf-8") as fh:
                setting = json.load(fh)["ai_setting"]
            if setting.get("llm_inference") == "local":
                local = setting["local_model_setting"]
                return "local:{} ({})".format(local["local_llm"]["name"], local["llm_backend"])
            cloud = setting["cloud_model_setting"]
            return "cloud:{} / {}".format(cloud["cloud_llm_provider"], cloud["cloud_llm"])
        except Exception:
            return "?"

    def summary(run, outcome):
        wall = time.monotonic() - run["start"]
        by_kind = run["kinds"]
        lines = ["  {:<12} {:>4} {:>8} {:>7} {:>7} {:>8} {:>7} {:>8} {:>5}".format(
            "kind", "n", "total", "mean", "max", "image", "cpu", "wait", "fail")]
        stats = {}
        for kind in sorted(by_kind):
            k = by_kind[kind]
            mean = k["total"] / k["n"] if k["n"] else 0.0
            stats[kind] = {"n": k["n"], "total": _seconds(k["total"]), "mean": _seconds(mean),
                           "max": _seconds(k["max"]), "image": _seconds(k["image"]),
                           "image_n": k["image_n"], "cpu": _seconds(k["cpu"]),
                           "wait": _seconds(k["wait"]), "fail": k["fail"]}
            lines.append("  {:<12} {:>4} {:>8.1f} {:>7.1f} {:>7.1f} {:>8.1f} {:>7.1f} {:>8.1f} {:>5}".format(
                kind, k["n"], k["total"], mean, k["max"], k["image"], k["cpu"], k["wait"], k["fail"]))
        generate = sum(k["total"] for k in by_kind.values())
        between = {
            "plan": {"n": run["plan_n"], "seconds": _seconds(run["plan_s"])},
            "commit": {"n": run["commit_n"], "seconds": _seconds(run["commit_s"])},
            "other": _seconds(max(0.0, wall - generate - run["plan_s"] - run["commit_s"])),
        }
        # 残りは画面へ渡った最後の値（`plan_tasks` の並びは済みを含むことがある。docstring）
        tasks = sum(k["n"] for k in by_kind.values())
        progress = run["progress"] or {}
        remaining = progress.get("remaining")
        estimate = None
        if isinstance(remaining, int) and tasks:
            estimate = remaining * generate / tasks
        emit("summary",
             "world={} runtime={} outcome={} wall={:.1f}s tasks={} max_active={}\n{}\n"
             "  between: plan {}回 {:.1f}s / commit {}回 {:.1f}s / other {:.1f}s\n"
             "  plan at start {}\n  plan at end   {}\n"
             "  screen at end {}\n"
             "  estimate for the rest: {}".format(
                 run["world"], run["runtime"], outcome, wall, tasks, run["max_active"],
                 "\n".join(lines),
                 run["plan_n"], run["plan_s"], run["commit_n"], run["commit_s"], between["other"],
                 run["plan_first"], run["plan_last"], progress or "(none)",
                 "{:.0f}s ({:.1f}h) = remaining {} x mean {:.1f}s".format(
                     estimate, estimate / 3600.0, remaining, generate / tasks)
                 if estimate is not None else "?"),
             world=run["world"], runtime=run["runtime"], outcome=outcome, wall=_seconds(wall),
             kinds=stats, between=between, max_active=run["max_active"],
             plan_start=run["plan_first"], plan_end=run["plan_last"], progress=progress,
             estimate=_seconds(estimate) if estimate is not None else None)

    @ctx.wrap(RUN_TARGET, required=False, safe=True)
    def run_bulk(orig, self, *args, **kwargs):
        run, stop = None, None
        try:
            stop = frames.arg(args, kwargs, "stop_event", 1)
            run = {
                "start": time.monotonic(),
                "world": str(frames.arg(args, kwargs, "world_path", 0)),
                "runtime": runtime_name(),
                "kinds": {}, "plan_n": 0, "plan_s": 0.0, "commit_n": 0, "commit_s": 0.0,
                "plan_first": None, "plan_last": None, "max_active": 0, "progress": None,
            }
            with lock:
                state["run"] = run
            emit("run_start", "world={} runtime={}".format(run["world"], run["runtime"]),
                 world=run["world"], runtime=run["runtime"])
        except Exception:
            ctx.log_exc("world bulk probe: cannot record the run start")
        outcome = "returned"
        try:
            return orig(self, *args, **kwargs)
        except BaseException as exc:
            outcome = "raised {}".format(type(exc).__name__)
            raise
        finally:
            if run is not None:
                with lock:
                    if state["run"] is run:
                        state["run"] = None
                try:
                    if outcome == "returned" and stop is not None and stop.is_set():
                        outcome = "stopped"
                    summary(run, outcome)
                except Exception:
                    ctx.log_exc("world bulk probe: cannot write the summary")

    # ------------------------------------------------------------ 画面の件数
    @ctx.wrap(PROGRESS_TARGET, required=False, safe=True)
    def update_progress(orig, self, *args, **kwargs):
        result = orig(self, *args, **kwargs)
        try:
            progress = frames.arg(args, kwargs, "state", 0)
            if isinstance(progress, dict):
                values = {key: progress.get(key) for key in PROGRESS_KEYS if key in progress}
                with lock:
                    run = state["run"]
                    if run is not None:
                        run["progress"] = values
                    changed = values != state.get("progress_seen")
                    state["progress_seen"] = values
                # 画面へは後から届くことがあり、実行の終わりの値はまとめに間に合わない。
                # 変わったときだけ表の方へ残し、走っていないとき（終わりの値）は読む方にも出す。
                if changed:
                    if run is None:
                        emit("progress", "{} (after the run)".format(values), progress=values)
                    else:
                        record({"at": now(), "kind": "progress", "progress": values})
        except Exception:
            ctx.log_exc("world bulk probe: cannot read the progress")
        return result

    # ------------------------------------------------------------ 仕事の並び
    @ctx.wrap(PLAN_TARGET, required=False, safe=True)
    def plan_tasks(orig, *args, **kwargs):
        start = time.monotonic()
        result = orig(*args, **kwargs)
        seconds = time.monotonic() - start
        try:
            counts = _count_kinds(result)
            with lock:
                run = state["run"]
                previous = None
                if run is not None:
                    previous = run["plan_last"]
                    run["plan_n"] += 1
                    run["plan_s"] += seconds
                    if run["plan_first"] is None:
                        run["plan_first"] = counts
                    run["plan_last"] = counts
            total = sum(counts.values()) if counts else None
            fields = dict(counts=counts, total=total, seconds=_seconds(seconds),
                          in_run=run is not None)
            grew = (previous is None or counts is None
                    or any(n > (previous or {}).get(kind, 0) for kind, n in counts.items()))
            if grew:
                emit("plan", "total={} {} ({:.2f}s){}".format(
                    total, counts, seconds, "" if run is not None else " (outside a run)"),
                    **fields)
            else:
                record(dict({"at": now(), "kind": "plan"}, **fields))
        except Exception:
            ctx.log_exc("world bulk probe: cannot count the plan")
        return result

    # ------------------------------------------------------------ 保存
    @ctx.wrap(COMMIT_TARGET, required=False, safe=True)
    def commit(orig, *args, **kwargs):
        start = time.monotonic()
        try:
            return orig(*args, **kwargs)
        finally:
            seconds = time.monotonic() - start
            with lock:
                run = state["run"]
                if run is not None:
                    run["commit_n"] += 1
                    run["commit_s"] += seconds

    # ------------------------------------------------------------ 仕事1件
    @ctx.wrap(TASK_TARGET, required=False, safe=True)
    def generate(orig, self, *args, **kwargs):
        row = None
        try:
            task = frames.arg(args, kwargs, "task", 0)
            with lock:
                state["seq"] += 1
                row = {
                    "seq": state["seq"], "kind": _kind_of(task),
                    "ids": frames.repr_value(frames.attr(task, "ids", None)),
                    "label": frames.short(str(frames.attr(task, "label", "")), LABEL_CHARS),
                    "start": time.monotonic(), "cpu0": time.thread_time(),
                    "thread_id": threading.get_ident(),
                    "image": 0.0, "image_n": 0, "image_cpu": 0.0,
                }
                active[row["seq"]] = row
                row["active"] = len(active)
                run = state["run"]
                if run is not None:
                    run["max_active"] = max(run["max_active"], len(active))
        except Exception:
            ctx.log_exc("world bulk probe: cannot record the task start")
        error = None
        try:
            return orig(self, *args, **kwargs)
        except BaseException as exc:
            error = "{}: {}".format(type(exc).__name__, frames.short(str(exc), 120))
            raise
        finally:
            if row is not None:
                try:
                    finish(row, error)
                except Exception:
                    ctx.log_exc("world bulk probe: cannot record the task end")

    def finish(row, error):
        total = time.monotonic() - row["start"]
        cpu = max(0.0, time.thread_time() - row["cpu0"] - row["image_cpu"])
        with lock:
            active.pop(row["seq"], None)
            image = row["image"]
            wait = max(0.0, total - image - cpu)
            run = state["run"]
            if run is not None:
                k = run["kinds"].setdefault(row["kind"], {
                    "n": 0, "total": 0.0, "max": 0.0, "image": 0.0, "image_n": 0,
                    "cpu": 0.0, "wait": 0.0, "fail": 0})
                k["n"] += 1
                k["total"] += total
                k["max"] = max(k["max"], total)
                k["image"] += image
                k["image_n"] += row["image_n"]
                k["cpu"] += cpu
                k["wait"] += wait
                if error:
                    k["fail"] += 1
        emit("task", "{} {:.1f}s image={:.1f}s({}) cpu={:.1f}s wait={:.1f}s active={} {}  {}".format(
            row["kind"], total, image, row["image_n"], cpu, wait, row["active"],
            "ok" if error is None else "FAIL " + error, row["label"]),
            task=row["kind"], ids=row["ids"], label=row["label"], seconds=_seconds(total),
            image=_seconds(image), image_n=row["image_n"], cpu=_seconds(cpu),
            wait=_seconds(wait), active=row["active"], error=error, in_run=run is not None)

    # ------------------------------------------------------------ 絵1枚
    def make_image(target):
        @ctx.wrap(target, required=False, safe=True)
        def image(orig, *args, **kwargs):
            if not active or getattr(marks, "drawing", False):
                return orig(*args, **kwargs)     # 一括生成の外か、内側の呼び出し
            marks.drawing = True
            start, cpu0 = time.monotonic(), time.thread_time()
            try:
                return orig(*args, **kwargs)
            finally:
                marks.drawing = False
                seconds = time.monotonic() - start
                cpu = time.thread_time() - cpu0
                here = threading.get_ident()
                with lock:
                    for row in active.values():
                        row["image"] += seconds
                        row["image_n"] += 1
                        if row["thread_id"] == here:
                            row["image_cpu"] += cpu
        return image

    for family in imagegen.families():
        for func in IMAGE_FUNCS:
            make_image("{}:{}".format(IMAGE_MODULE_FORMAT.format(family), func))
