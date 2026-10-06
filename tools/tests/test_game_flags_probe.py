# -*- coding: utf-8 -*-
"""239_probe_game_flags をゲーム抜きで通す。

    python tools/tests/test_game_flags_probe.py

確認するもの:

  素通り   … 見張りを置いても値の読み書きは変わらない（`getattr` の既定値・クラスの既定値も）
  書き込み … 値が変わると `set` 行に旧値・新値・居場所が出る。同じ値の書き直しは呼び出し元ごとに初回だけ
  読み取り … 読んだ関数ごとに初回だけ `read` 行
  当て直し … もう一度 apply しても見張りは積み上がらず、1回の書き込みは1行
  保存     … 見張りを通らない書き換え（`__dict__` の直書き）が `bypass` に出る
  ロード   … ロードの後の旗が `load` 行に出る
  例外     … 書き手が落ちても値は書かれる
"""
import importlib.util
import io
import json
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
RUNTIME_DIR = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir, "runtime"))
MOD_DIR = os.path.join(RUNTIME_DIR, "mods", "239_probe_game_flags")
OUT_DIR = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir, "out", "test",
                                        "game_flags_probe"))
RECORD_NAME = "game_flags.jsonl"

if RUNTIME_DIR not in sys.path:
    sys.path.insert(0, RUNTIME_DIR)

import instantale_modloader as ml                      # noqa: E402

failures = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name
          + ((" -- " + str(detail)) if detail and not cond else ""))
    if not cond:
        failures.append(name)


class PhaseSpec(object):
    def __init__(self, cls_name, args=()):
        self.cls_name = cls_name
        self.args = list(args)


class Facility(object):
    def __init__(self, name, facility_type):
        self.name = name
        self.facility_type = facility_type


class Player(object):
    def __init__(self, location):
        self.location = location


class InstantaleApp(object):
    """見張りはこのスクリプト（`__main__`）のこのクラスに置かれる。"""
    in_boss_battle = 0                 # クラスが既定値を持つ旗（インスタンスには置かない）

    def __init__(self):
        self.player = Player(Facility("雑貨屋", "general_store"))
        self.in_battle = 0
        self.in_conversation = None
        self.in_shopping = False
        self.is_popup_window_opened = False
        self.buttons = [{"text": "出る", "spec": PhaseSpec("MovePhaseManager")},
                        {"text": "入口", "spec": PhaseSpec("MovePhaseManager")}]

    def save_game(self):
        return "saved"

    def load_game_new(self):
        self.in_shopping = True        # セーブに焼かれた値が戻る
        return "loaded"


class FakeUI(object):
    def __init__(self, app, real):
        self._app = app
        self._real = real

    def find_app(self):
        return self._app

    def current_area(self, app):
        return None

    def __getattr__(self, name):
        return getattr(self._real, name)


class FakeCtx(object):
    _mod = "239_probe_game_flags"

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

    def jsonl(self, name, **kw):
        return ml.ModContext.jsonl(self, name, **kw)

    def log(self, msg, level="INFO"):
        pass

    def log_exc(self, msg):
        self.errors.append(msg)

    def wrap(self, target, **kw):
        def decorator(func):
            self.hooks[target] = func
            return func
        return decorator


def load_mod():
    name = "game_flags_probe_mod"
    sys.modules.pop(name, None)
    with io.open(os.path.join(MOD_DIR, "mod.json"), encoding="utf-8") as fh:
        entry = json.load(fh)["entry"]
    spec = importlib.util.spec_from_file_location(name, os.path.join(MOD_DIR, entry))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def apply(app):
    module = load_mod()
    module.ui = FakeUI(app, ml.ui)
    ctx = FakeCtx(OUT_DIR)
    module.apply(ctx)
    return module, ctx


def records(kind=None):
    path = os.path.join(OUT_DIR, RECORD_NAME)
    if not os.path.exists(path):
        return []
    with io.open(path, encoding="utf-8") as fh:
        rows = [json.loads(line) for line in fh if line.strip()]
    return [row for row in rows if kind is None or row.get("kind") == kind]


def touch_shopping(app, value):
    app.in_shopping = value            # 呼び出し元を1か所に固定する


shutil.rmtree(OUT_DIR, ignore_errors=True)
os.makedirs(OUT_DIR, exist_ok=True)
app = InstantaleApp()
module, ctx = apply(app)

print("素通り")
check("見張りがクラスに置かれた", type(vars(InstantaleApp)["in_shopping"]).__name__ == "FlagWatch")
check("インスタンスの値をそのまま返す", app.in_shopping is False and app.in_battle == 0)
check("クラスの既定値を返す", app.in_boss_battle == 0)
check("無い旗は getattr の既定値", getattr(app, "in_free_input", "none") == "none")
check("値はインスタンスの __dict__ に入ったまま", vars(app).get("in_shopping") is False)

print("書き込み")
touch_shopping(app, True)
sets = [r for r in records("set") if r["flag"] == "in_shopping"]
check("値が変わると set 行", len(sets) == 1 and sets[0]["old"] == "False"
      and sets[0]["new"] == "True", sets)
check("居場所が入る", sets and sets[0]["where"].get("type") == "general_store", sets)
check("値は書かれた", app.in_shopping is True and vars(app)["in_shopping"] is True)
touch_shopping(app, True)
touch_shopping(app, True)
rewrites = [r for r in records("rewrite") if r["flag"] == "in_shopping"]
check("同じ値の書き直しは呼び出し元ごとに1行", len(rewrites) == 1, rewrites)
app.in_conversation = "63"
conv = [r for r in records("set") if r["flag"] == "in_conversation"]
check("会話の相手の id も録る", conv and conv[-1]["new"] == "'63'", conv)

print("読み取り")
def read_battle(app):
    return getattr(app, "in_battle", 0)


before = len(records("read"))
for _ in range(5):
    read_battle(app)
reads = [r for r in records("read") if r["flag"] == "in_battle"]
check("読んだ関数ごとに初回だけ", len(records("read")) - before == 1
      and any("read_battle" in r["site"] for r in reads), reads)

print("当て直し")
watch = vars(InstantaleApp)["in_shopping"]
module, ctx = apply(app)
check("見張りは置き直さない", vars(InstantaleApp)["in_shopping"] is watch)
count = len(records("set"))
touch_shopping(app, False)
check("1回の書き込みは1行", len(records("set")) == count + 1, len(records("set")) - count)
check("値は保たれている", app.in_shopping is False and app.in_battle == 0)

print("保存")
vars(app)["in_shopping"] = True     # 見張りを通らない書き換え
result = ctx.hooks["__main__:InstantaleApp.save_game"](InstantaleApp.save_game, app)
saves = records("save")
check("保存の戻り値はそのまま", result == "saved")
check("属性を通らない書き換えが bypass に出る",
      saves and "in_shopping" in (saves[-1].get("bypass") or {}), saves)
check("保存の前の旗が出る", saves and saves[-1]["flags"].get("in_shopping") == "True", saves)
check("保存の行に選択肢のクラスが出る", saves and saves[-1].get("screen") == ["MovePhaseManager"], saves)

print("ロード")
vars(app)["in_shopping"] = False
result = ctx.hooks["__main__:InstantaleApp.load_game_new"](InstantaleApp.load_game_new, app)
loads = records("load")
check("ロードの戻り値はそのまま", result == "loaded")
check("ロードの後の旗が出る", loads and loads[-1]["flags"].get("in_shopping") == "True", loads)

print("例外")
store = getattr(sys, module.STORE)
sink = store["sink"]


def broken(*_args):
    raise RuntimeError("boom")


store["sink"] = broken
app.in_battle = "normal"
store["sink"] = sink
check("書き手が落ちても値は書かれる", app.in_battle == "normal")
check("ログの書き出しで落ちていない", not ctx.errors, ctx.errors)

print()
if failures:
    print("FAILED: {}".format(", ".join(failures)))
    sys.exit(1)
print("all checks passed")
