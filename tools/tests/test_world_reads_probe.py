# -*- coding: utf-8 -*-
"""242_probe_world_reads をゲーム抜きで通す。

    python tools/tests/test_world_reads_probe.py

確認するもの:

  差し替え … `app` の型を同じ名前の派生クラスに替える。`world_dict` は同じ辞書を返し、代入も元の置き場へ入る
  記録     … 引いた箇所（関数・行）ごとに初回だけ1行、以後は数だけ増やす。代入も記録する
  書き手   … 書き手に渡ったファイルを名前ごと・呼び出しの連鎖ごとに数える
  一覧     … `out/world_reads.json` に箇所と回数を書く
  段       … `TRACE_PATHS` の間だけ、対象の関数が引いた段を記録する。書き込みは元の辞書にも通し、窓が終われば元の辞書に戻す
"""
import importlib.util
import io
import json
import os
import shutil
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
RUNTIME_DIR = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir, "runtime"))
MODS_DIR = os.path.join(RUNTIME_DIR, "mods")
OUT_DIR = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir, "out", "test", "world_reads"))

if RUNTIME_DIR not in sys.path:
    sys.path.insert(0, RUNTIME_DIR)

import instantale_modloader as ml                      # noqa: E402
from instantale_modloader import ui                    # noqa: E402

failures = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name
          + ((" -- " + str(detail)) if detail and not cond else ""))
    if not cond:
        failures.append(name)


class FakeClock:
    def __init__(self):
        self.intervals = []
        self.onces = []

    def schedule_interval(self, fn, seconds):
        self.intervals.append(fn)

    def schedule_once(self, fn, seconds=0):
        self.onces.append(fn)


CLOCK = FakeClock()
kivy = types.ModuleType("kivy")
kivy_clock = types.ModuleType("kivy.clock")
kivy_clock.Clock = CLOCK
sys.modules["kivy"] = kivy
sys.modules["kivy.clock"] = kivy_clock


class InstantaleApp:
    pass


class FakeCtx:
    _mod = "242_probe_world_reads"

    def __init__(self, out_dir):
        self.out_dir = out_dir
        self.hooks = {}
        self.errors = []

    def out_path(self, *parts):
        path = os.path.join(self.out_dir, *parts)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        return path

    def logger(self, name, **kw):
        return ml.ModContext.logger(self, name, **kw)

    def log(self, msg, level="INFO"):
        pass

    def log_exc(self, msg):
        self.errors.append(msg)

    def superseded(self):
        return False

    def write_json(self, path, data, *, indent=1):
        return ml.write_json(path, data, indent=indent, report=self.log_exc)

    def wrap(self, target, **kw):
        def decorator(func):
            self.hooks[target] = func
            return func
        return decorator


shutil.rmtree(OUT_DIR, ignore_errors=True)
os.makedirs(OUT_DIR, exist_ok=True)
folder = os.path.join(MODS_DIR, "242_probe_world_reads")
with io.open(os.path.join(folder, "mod.json"), encoding="utf-8") as fh:
    entry = json.load(fh)["entry"]
spec = importlib.util.spec_from_file_location("probe_world_reads_mod", os.path.join(folder, entry))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
if hasattr(sys, module.STORE):
    delattr(sys, module.STORE)

app = InstantaleApp()
world = {"areas": {"8": {}}}
app.world_dict = world
app.player = types.SimpleNamespace(location=None)
saved_find, ui.find_app = ui.find_app, lambda: app
try:
    ctx = FakeCtx(OUT_DIR)
    module.apply(ctx)
    for fn in CLOCK.onces:
        fn(0)

    print("差し替え")
    check("型の名前は元のまま", type(app).__name__ == "InstantaleApp", type(app))
    check("元の型の派生", isinstance(app, InstantaleApp) and type(app) is not InstantaleApp)
    check("同じ辞書を返す", app.world_dict is world)

    print("記録")

    def game_shop():
        return app.world_dict["areas"]

    for _ in range(3):
        game_shop()
    store = getattr(sys, module.STORE)
    reads = [e for e in store["sites"].values() if e["kind"] == "read" and "game_shop" in e["site"]]
    check("箇所は1件", len(reads) == 1, store["sites"])
    check("回数は3", reads and reads[0]["count"] == 3, reads)
    replaced = {"areas": {}}
    app.world_dict = replaced
    check("代入は元の置き場へ", app.__dict__["world_dict"] is replaced and app.world_dict is replaced)
    check("代入も記録する", any(e["kind"] == "set" for e in store["sites"].values()), store["sites"])
    with io.open(os.path.join(OUT_DIR, "world_reads.log"), encoding="utf-8") as fh:
        log = fh.read()
    check("初回だけ1行", log.count("game_shop") == 1, log)

    print("書き手")
    hook = ctx.hooks[module.WRITE_TARGET]
    written = []
    for path in ("C:/x/worlds/w/world_data.json", "C:/x/saves/w/savedata.json", "C:/x/worlds/w/world_data.json"):
        hook(lambda p, d: written.append(p), path, {})
    check("書き手は素通し", len(written) == 3, written)
    counts = {e["file"]: e["count"] for e in store["writes"].values()}
    check("ファイル名ごとに数える", counts == {"world_data.json": 2, "savedata.json": 1}, store["writes"])
    rhook = ctx.hooks["scripts.save_codec:read_obfuscated_json_file"]
    rhook(lambda p: {}, "C:/x/worlds/w/world_data.json")
    check("読み込みも数える", any(e.get("kind") == "read" and e["file"] == "world_data.json" for e in store["writes"].values()), store["writes"])

    print("一覧")
    for fn in CLOCK.intervals:
        fn(0)
    with io.open(os.path.join(OUT_DIR, "world_reads.json"), encoding="utf-8") as fh:
        summary = json.load(fh)
    check("箇所と回数が並ぶ", summary["sites"] and summary["sites"][0]["count"] >= 3, summary)
    check("書き手も並ぶ", summary["world_file_writes"] and summary["world_file_writes"][0]["count"] == 2, summary)
    print("段の記録")
    module.TRACE_PATHS = True
    original = {"areas": {"8": {"nodes": {"32": {"facilities": {"228": {"config": {}}}}}}}, "npcs": {"98": {}}}
    app.world_dict = original

    class Facility:
        pass

    def facility_init(self, owner):
        facs = app.world_dict["areas"]["8"]["nodes"]["32"]["facilities"]
        self.config = facs["228"]["config"]
        self.config["goods"] = ["品"]
        self.has_owner = owner in app.world_dict["npcs"]

    thook = ctx.hooks["__main__:Facility.__init__"]
    fac = Facility()
    thook(facility_init, fac, "98")
    check("窓が終わったら元の辞書に戻す", app.__dict__["world_dict"] is original)
    check("書き込みは元の辞書にも通る",
          original["areas"]["8"]["nodes"]["32"]["facilities"]["228"]["config"].get("goods") == ["品"], original)
    check("読んだ段を記録する（id はまとめる）",
          "get areas/<id>/nodes/<id>/facilities/<id>/config" in store["paths"].get("Facility.__init__", {}),
          store["paths"])
    check("in も記録する", "in npcs/<id>" in store["paths"]["Facility.__init__"], store["paths"])
    n = len(store["paths"]["Facility.__init__"])
    fac.config["after"] = 1
    check("窓の外では記録しない", len(store["paths"]["Facility.__init__"]) == n)
    check("窓の外の書き込みも元の辞書に通る",
          original["areas"]["8"]["nodes"]["32"]["facilities"]["228"]["config"].get("after") == 1)
    module.TRACE_PATHS = False
    calls = []
    thook(lambda self, o: calls.append(type(app.world_dict).__name__), Facility(), "98")
    check("切っていれば写しにしない", calls == ["dict"], calls)
    check("例外を出していない", not ctx.errors, ctx.errors)
finally:
    ui.find_app = saved_find

if failures:
    print("FAILED: " + ", ".join(failures))
    sys.exit(1)
print("OK")
