# -*- coding: utf-8 -*-
"""241_probe_world_bulk をゲーム抜きで通す。

    python tools/tests/test_world_bulk_probe.py

確認するもの:

  素通り   … 引数・戻り値・例外はそのまま。世界の書き込みも素のまま
  仕事     … 仕事1件ごとに `task` 行。種類・所要・絵の時間と枚数・同時に走っていた数
  絵       … 描く関数が入れ子に呼ばれても1枚と数える。一括生成の外の絵は数えない
  並び     … 始めの並びは全部、後は種類の件数が増えたときだけ読む方のログに出す
  まとめ   … 走り終えたら種類ごとの表・失敗の数・保存の回数・残りの見込み。中断も分かる
  外       … 一括生成の外で呼ばれた仕事も落ちずに録る
"""
import importlib.util
import io
import json
import os
import shutil
import sys
import threading
import time
import types

HERE = os.path.dirname(os.path.abspath(__file__))
RUNTIME_DIR = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir, "runtime"))
MOD_DIR = os.path.join(RUNTIME_DIR, "mods", "241_probe_world_bulk")
OUT_DIR = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir, "out", "test",
                                        "world_bulk_probe"))
RECORD_NAME = "world_bulk.jsonl"
LOG_NAME = "world_bulk.log"

if RUNTIME_DIR not in sys.path:
    sys.path.insert(0, RUNTIME_DIR)

import instantale_modloader as ml                      # noqa: E402

failures = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name
          + ((" -- " + str(detail)) if detail and not cond else ""))
    if not cond:
        failures.append(name)


# ------------------------------------------------------------ 偽のゲーム
IMAGE_MODULE = "image_generation.fam.stable_diffusion_manager"
images = types.ModuleType(IMAGE_MODULE)


def generate_image_anime(prompt, depth=0):
    time.sleep(0.03)
    if depth == 0:
        images.image_to_image_anime(prompt)       # 本体の中で別の描く関数を呼ぶ形
    return "img:" + prompt


def image_to_image_anime(prompt):
    time.sleep(0.01)
    return "i2i:" + prompt


def generate_image_real_lcm(prompt):
    time.sleep(0.02)
    return "bg:" + prompt


images.generate_image_anime = generate_image_anime
images.image_to_image_anime = image_to_image_anime
images.generate_image_real_lcm = generate_image_real_lcm
sys.modules[IMAGE_MODULE] = images


class Task(object):
    def __init__(self, kind, ids, label):
        self.kind, self.ids, self.label = kind, ids, label


backend_module = types.ModuleType("scripts.world_bulk_backend")


class GameGenerationBackend(object):
    def __init__(self):
        self.calls = []

    def generate(self, task, world):
        self.calls.append(task.kind)
        if task.kind == "area":
            time.sleep(0.05)                       # LLM の待ちの代わり
            world["areas"].append(task.ids)
            world["npcs"].append(task.ids + "-npc")
        elif task.kind == "portrait":
            images.generate_image_anime(task.label)
        elif task.kind == "background":
            images.generate_image_real_lcm(task.label)
        elif task.kind == "npc":
            raise RuntimeError("LLM failed for " + task.ids)
        return "done:" + task.ids


backend_module.GameGenerationBackend = GameGenerationBackend
sys.modules["scripts.world_bulk_backend"] = backend_module

generation = types.ModuleType("scripts.world_bulk_generation")


def plan_tasks(world, root):
    """人物ごとに詳細と絵、まだのエリア、背景の順。済んだ（試した）ものは外す。"""
    tasks = []
    for npc in world["npcs"]:
        tasks.append(Task("npc", npc, "NPC " + npc))
        tasks.append(Task("portrait", npc, "絵 " + npc))
    for area in world["pending_areas"]:
        if area not in world["areas"]:
            tasks.append(Task("area", area, "エリア " + area))
    tasks.append(Task("background", "bg", "背景"))
    return [task for task in tasks if [task.kind, task.ids] not in world["done"]]


written = []


def atomic_world_write(path, world):
    written.append((path, json.dumps(world, sort_keys=True)))


class BulkGenerator(object):
    """`plan_tasks` の先頭から1件ずつ。失敗は数えて次へ（本体の作りの見かけ）。"""

    def __init__(self, world_path, backend, stop=None, limit=10, progress=None):
        self.world_path = world_path
        self.backend = backend
        self.stop = stop or threading.Event()
        self.limit = limit
        self.failures = []
        self.progress = progress or (lambda state: None)

    def run(self):
        world = {"areas": [], "npcs": ["n1"], "done": [], "pending_areas": ["a1"]}
        for _ in range(self.limit):
            if self.stop.is_set():
                break
            tasks = generation.plan_tasks(world, "root")
            if not tasks:
                break
            task = tasks[0]
            try:
                self.backend.generate(task, world)
            except RuntimeError as exc:
                self.failures.append(str(exc))
            world["done"].append([task.kind, task.ids])
            generation.atomic_world_write(self.world_path, world)
            self.progress({"status": "running", "completed": len(world["done"]),
                           "remaining": len(plan_tasks(world, "root")),     # 素の関数（数に入れない）
                           "failed": len(self.failures), "kind": task.kind})
        return {"world": world, "failures": len(self.failures)}


generation.plan_tasks = plan_tasks
generation.atomic_world_write = atomic_world_write
generation.BulkGenerator = BulkGenerator
sys.modules["scripts.world_bulk_generation"] = generation


hud_module = types.ModuleType("scripts.hud.hud_world_bulk")


class WorldBulkScreen(object):
    """画面の件数（`update_progress(state)`）。"""

    def __init__(self):
        self.states = []

    def update_progress(self, state):
        self.states.append(state)


hud_module.WorldBulkScreen = WorldBulkScreen
sys.modules["scripts.hud.hud_world_bulk"] = hud_module


class InstantaleApp(object):
    """実行の境界（`__main__:InstantaleApp.run_world_bulk_generation`）。このテスト自身が `__main__`。"""

    def __init__(self, backend):
        self.backend = backend
        self.generator = None

    def run_world_bulk_generation(self, world_path, stop_event, progress):
        self.screen = WorldBulkScreen()
        self.generator = BulkGenerator(world_path, self.backend, stop=stop_event,
                                       progress=self.screen.update_progress)
        return self.generator.run()


def run_bulk(world_path, backend, stop=None):
    app = InstantaleApp(backend)
    result = app.run_world_bulk_generation(world_path, stop or threading.Event(), None)
    return app, result


# ------------------------------------------------------------ 偽の ctx
class FakeCtx(object):
    _mod = "241_probe_world_bulk"

    def __init__(self, out_dir):
        self.out_dir = out_dir
        self.errors = []
        self.targets = []

    def superseded(self):
        return False

    def out_path(self, *parts):
        path = os.path.join(self.out_dir, *parts)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        return path

    def logger(self, name, **kw):
        return ml.ModContext.logger(self, name, **kw)

    def jsonl(self, name, **kw):
        return ml.ModContext.jsonl(self, name, **kw)

    def log(self, msg, level="INFO"):
        pass

    def log_exc(self, msg):
        self.errors.append(msg)

    def wrap(self, target, **kw):
        """本物と同じく、対象を差し替えて `func(orig, ...)` を呼ばせる（無い対象は飛ばす）。"""
        def decorator(func):
            module_name, _, path = target.partition(":")
            owner = sys.modules.get(module_name)
            parts = path.split(".")
            for part in parts[:-1]:
                owner = getattr(owner, part, None) if owner is not None else None
            if owner is None or not hasattr(owner, parts[-1]):
                return func
            # 当て直しは前の層を外してから（本物の `unwrap_ours` と同じ）
            old = getattr(owner, parts[-1])
            old = getattr(old, "__original__", old)

            def wrapper(*args, **kwargs):
                return func(old, *args, **kwargs)
            wrapper.__original__ = old
            setattr(owner, parts[-1], wrapper)
            self.targets.append(target)
            return func
        return decorator


class FakeImagegen(object):
    @staticmethod
    def families(name=None):
        return ["fam"]


def load_mod():
    name = "world_bulk_probe_mod"
    sys.modules.pop(name, None)
    with io.open(os.path.join(MOD_DIR, "mod.json"), encoding="utf-8") as fh:
        entry = json.load(fh)["entry"]
    spec = importlib.util.spec_from_file_location(name, os.path.join(MOD_DIR, entry))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def records(kind=None):
    path = os.path.join(OUT_DIR, RECORD_NAME)
    if not os.path.exists(path):
        return []
    with io.open(path, encoding="utf-8") as fh:
        rows = [json.loads(line) for line in fh if line.strip()]
    return [row for row in rows if kind is None or row.get("kind") == kind]


def log_text():
    path = os.path.join(OUT_DIR, LOG_NAME)
    if not os.path.exists(path):
        return ""
    with io.open(path, encoding="utf-8") as fh:
        return fh.read()


shutil.rmtree(OUT_DIR, ignore_errors=True)
os.makedirs(OUT_DIR, exist_ok=True)
module = load_mod()
module.imagegen = FakeImagegen()
ctx = FakeCtx(OUT_DIR)
module.apply(ctx)

print("仕掛け")
check("5か所と絵の3関数を包んだ", len(ctx.targets) == 8, ctx.targets)

print("絵（一括生成の外）")
check("外の絵は素通りで数えない", images.generate_image_real_lcm("x") == "bg:x"
      and not records(), records())

print("素通り")
backend = GameGenerationBackend()
app, result = run_bulk("worlds/w", backend)
generator = app.generator
expected_order = ["npc", "portrait", "area", "npc", "portrait", "background"]
check("仕事の順と中身はそのまま", backend.calls == expected_order, backend.calls)
check("戻り値はそのまま", result["failures"] == 2 and result["world"]["areas"] == ["a1"], result)
check("失敗はそのまま本体へ投げた", len(generator.failures) == 2
      and "LLM failed for n1" in generator.failures[0], generator.failures)
check("世界の書き込みは1件ごと", len(written) == 6, len(written))
check("包みのエラーは無い", not ctx.errors, ctx.errors)

print("仕事")
tasks = records("task")
check("task 行は仕事の数", len(tasks) == 6, len(tasks))
by = {}
for row in tasks:
    by.setdefault(row["task"], []).append(row)
area = by.get("area", [{}])[0]
check("エリアは待ちに入る", area.get("seconds", 0) >= 0.05 and area.get("image") == 0
      and area.get("wait", 0) >= 0.04, area)
portrait = by.get("portrait", [{}])[0]
check("入れ子の絵は1枚と数える", portrait.get("image_n") == 1, portrait)
check("絵の時間は外側の1回分", portrait.get("image", 0) >= 0.04, portrait)
check("絵の時間は待ちに入らない", portrait.get("wait", 1) < 0.03, portrait)
npc = by.get("npc", [{}])[0]
check("失敗は理由つき", (npc.get("error") or "").startswith("RuntimeError: LLM failed"), npc)
check("1件ずつ", all(row["active"] == 1 for row in tasks), [r["active"] for r in tasks])
check("名前と ids が出る", area.get("label") == "エリア a1" and area.get("ids") == "'a1'", area)

print("並び")
plans = records("plan")
check("並べ直しは毎回 jsonl に（最後の空の1回を含む）", len(plans) == 7, len(plans))
check("始めの並びの内訳", plans and plans[0]["counts"] == {"area": 1, "background": 1, "npc": 1,
                                                       "portrait": 1}, plans[:1])
text = log_text()
plan_lines = [line for line in text.splitlines() if "] plan " in line]
check("読む方には始めと増えたときだけ", len(plan_lines) == 2, plan_lines)

print("まとめ")
summaries = records("summary")
check("summary は1行", len(summaries) == 1, len(summaries))
summary = summaries[0] if summaries else {}
kinds = summary.get("kinds", {})
check("種類ごとの件数", {k: v["n"] for k, v in kinds.items()}
      == {"area": 1, "background": 1, "npc": 2, "portrait": 2}, kinds)
check("失敗の数", kinds.get("npc", {}).get("fail") == 2, kinds.get("npc"))
check("保存の回数", summary.get("between", {}).get("commit", {}).get("n") == 6, summary.get("between"))
check("並べ直しの回数", summary.get("between", {}).get("plan", {}).get("n") == 7, summary.get("between"))
check("同時に走ったのは最大1", summary.get("max_active") == 1, summary.get("max_active"))
check("最後まで流れた", summary.get("outcome") == "returned", summary.get("outcome"))
check("始めの並び", summary.get("plan_start") == plans[0]["counts"] if plans else False,
      summary.get("plan_start"))
check("画面の件数", summary.get("progress") == {"status": "running", "completed": 6,
                                                "remaining": 0, "failed": 2}, summary.get("progress"))
check("画面の件数は素通り", app.screen.states and app.screen.states[-1]["kind"] == "background",
      app.screen.states[-1:])
check("表が読む方に出る", "estimate for the rest" in text and "portrait" in text)

print("中断と見込み")
stop = threading.Event()


class StoppingBackend(GameGenerationBackend):
    def generate(self, task, world):
        result = GameGenerationBackend.generate(self, task, world)
        stop.set()                                  # 成功した1件目の後で「中断する」
        return result


before = len(records("summary"))
run_bulk("worlds/w2", StoppingBackend(), stop=stop)
summary = records("summary")[-1]
check("中断が分かる", len(records("summary")) == before + 1 and summary["outcome"] == "stopped",
      summary.get("outcome"))
check("残りの見込みは画面の残り × 1件の平均", summary["progress"]["remaining"] == 2
      and summary["estimate"] > 0, (summary["progress"], summary["estimate"]))

print("例外の実行")


class Boom(Exception):
    pass


raised = False
original_plan = generation.plan_tasks
generation.plan_tasks = lambda world, root: (_ for _ in ()).throw(Boom("plan"))
try:
    run_bulk("worlds/w3", GameGenerationBackend())
except Boom:
    raised = True
generation.plan_tasks = original_plan
check("本体の例外はそのまま投げる", raised)
check("投げた実行もまとめを書く", records("summary")[-1]["outcome"] == "raised Boom",
      records("summary")[-1].get("outcome"))

print("実行の途中の当て直し")
# 実機では「開始／再開」の後に一括生成のモジュールが読み込まれ、ローダが MOD を当て直す
# （boot #3）。走っている実行は前の包みの中に居る。
later = {}


class ReapplyingBackend(GameGenerationBackend):
    def generate(self, task, world):
        if not later:
            later["ctx"] = FakeCtx(OUT_DIR)
            module.apply(later["ctx"])
        return GameGenerationBackend.generate(self, task, world)


before = len(records("summary"))
run_bulk("worlds/w4", ReapplyingBackend())
summary = records("summary")[-1]
check("前の包みがまとめを書く", len(records("summary")) == before + 1
      and summary["world"] == "worlds/w4", summary.get("world"))
check("当て直しの後の仕事もまとめに入る", sum(v["n"] for v in summary["kinds"].values()) == 6
      and summary["between"]["commit"]["n"] == 6, (summary["kinds"], summary["between"]))
check("当て直しで包みは積み上がらない", summary["max_active"] == 1
      and summary["kinds"]["portrait"]["image_n"] == 2, summary["kinds"].get("portrait"))
check("当て直した方もエラーは無い", not later["ctx"].errors, later["ctx"].errors)

print("外")
lone = GameGenerationBackend().generate(Task("area", "a9", "外"), {"areas": [], "npcs": []})
row = records("task")[-1]
check("一括生成の外の仕事も録る", lone == "done:a9" and row["in_run"] is False, row)
screen = WorldBulkScreen()
screen.update_progress({"status": "complete", "completed": 6, "remaining": 0, "failed": 0})
screen.update_progress({"status": "complete", "completed": 6, "remaining": 0, "failed": 0})
after = records("progress")
check("実行の後に届いた画面の件数も残る", after and after[-1]["progress"]["status"] == "complete"
      and "(after the run)" in log_text(), after[-1:])
check("同じ件数は1行だけ", len([r for r in after if r["progress"].get("status") == "complete"]) == 1,
      after[-3:])
check("最後までエラーは無い", not ctx.errors, ctx.errors)

if failures:
    print("\nFAILED: {} check(s)".format(len(failures)))
    sys.exit(1)
print("\nall passed")
