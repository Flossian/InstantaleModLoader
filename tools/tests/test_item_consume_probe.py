# -*- coding: utf-8 -*-
"""226_probe_item_consume をゲーム抜きで通す。

    python tools/tests/test_item_consume_probe.py

偽の `ui` と偽のゲーム関数を差し込み、次を確認する。

  経路     … 対象が全部登録される（打ち間違えは `required=False` で黙って降りる）
  差分     … `consume_item` の前後でプレイヤーの HP・スタミナ・持ち物の数の差分が録られ、
             `usable` の実値と、その間に足された文が同じ行に載る。
             品の持ち主がプレイヤーでなければ持ち主の差分も別に載る
  変えない … 本体が1回だけ呼ばれ、戻り値も引数もそのまま通る。
             `add_text` は使用の外では何も溜めない
  popup    … 押下の記録に popup の項目が値ごと（数・真偽・文字列）で載る
  純関数   … 同じ引数の組は1度だけ
  壊れても … 記録が失敗しても本体は呼ばれ、戻り値は変わらない
  上限     … 使用の外の上限更新は UPDATE_SAMPLES 件で打ち切る
"""
import importlib.util
import io
import json
import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
RUNTIME_DIR = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir, "runtime"))
MODS_DIR = os.path.join(RUNTIME_DIR, "mods")
OUT_DIR = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir, "out", "test"))

if RUNTIME_DIR not in sys.path:
    sys.path.insert(0, RUNTIME_DIR)

TARGETS = (
    "__main__:ItemConsumeManager.consume_item",
    "__main__:ItemConsumeManager.execute",
    "__main__:ItemUseManager.use_item",
    "__main__:ItemUseManager.execute",
    "__main__:InstantaleApp.add_text",
    "scripts.hud.new_hud:ItemPopupMenu.on_consume_item",
    "scripts.hud.new_hud:ItemPopupMenu.on_use_item",
    "scripts.items:Item.consume",
    "scripts.items:Item.use",
    "scripts.functions:get_heal_spec",
    "scripts.functions:get_heal_physical_integrity_barden",
    "scripts.functions:get_max_physical_integrity",
    "scripts.characters:Character.update_max_physical_integrity",
    "scripts.characters:Character.update_max_hp",
)

RECORD_NAME = "item_consume.jsonl"
LOG_NAME = "item_consume.log"


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


MOD_DIR, MOD = find_mod("_probe_item_consume")

failures = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name
          + ((" -- " + str(detail)) if detail and not cond else ""))
    if not cond:
        failures.append(name)


# ---------------------------------------------------------------- 偽ゲーム
class Character(object):
    def __init__(self, name, hp=100, stamina=10, items=3):
        self.name = name
        self.current_hp = hp
        self.max_hp = 120
        self.original_max_hp = 150
        self.physical_integrity = stamina
        self.max_physical_integrity = 10
        self.original_max_physical_integrity = 100
        self.exhausted = False
        self.experience_level = 1
        self.status = {}
        self.ability_scores = {"strength": 10}
        self.gold = 50
        self.inventory = {str(i): object() for i in range(items)}


class Item(object):
    def __init__(self, name, obtainer, heal=20, burden=10):
        self.id = "7"
        self.name = name
        self.item_type = "healing_item"
        self.value = 3
        self.rarity = "common"
        self.attributes = {"item_detail": "drink", "回復": heal, "疲労負荷": burden,
                           "買価": 50}
        self.obtainer = obtainer


class App(object):
    def __init__(self, player):
        self.player = player
        self.texts = []

    def add_text(self, context):
        self.texts.append(context)


class Manager(object):
    def __init__(self, app):
        self.app = app


class FakeUI(object):
    def __init__(self, app):
        self.app = app

    def find_app(self):
        return self.app


class FakeCtx(object):
    def __init__(self, out_dir):
        self.out_dir = out_dir
        self.hooks = {}
        self.errors = []
        self.logs = []

    def out_path(self, *parts):
        path = os.path.join(self.out_dir, *parts)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        return path

    _mod = "226_probe_item_consume"

    def logger(self, name, *, tag=None, stamp=True, label=None):
        import instantale_modloader as _ml
        return _ml.ModContext.logger(self, name, tag=tag, stamp=stamp, label=label)

    def log(self, msg, level="INFO"):
        self.logs.append((level, msg))

    def log_exc(self, msg):
        self.errors.append(msg)

    def wrap(self, target, **kw):
        def decorator(func):
            self.hooks[target] = func
            return func
        return decorator


def load_mod(name="item_consume_probe_mod"):
    sys.modules.pop(name, None)
    spec = importlib.util.spec_from_file_location(
        name, MOD, submodule_search_locations=[MOD_DIR])
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def fresh_mod(app):
    for name in (RECORD_NAME, LOG_NAME):
        path = os.path.join(OUT_DIR, name)
        if os.path.exists(path):
            os.remove(path)
    module = load_mod()
    module.ui = FakeUI(app)
    ctx = FakeCtx(OUT_DIR)
    module.apply(ctx)
    # 本物では `add_text` の包みがクラスに当たる。
    # 偽の app では本体の呼び出しを包み越しに通す（文を拾う経路そのものを検査したい）。
    hook = ctx.hooks["__main__:InstantaleApp.add_text"]
    app.add_text = lambda context: hook(App.add_text, app, context)
    return module, ctx


def read_records():
    path = os.path.join(OUT_DIR, RECORD_NAME)
    if not os.path.exists(path):
        return []
    with io.open(path, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def read_log():
    path = os.path.join(OUT_DIR, LOG_NAME)
    if not os.path.exists(path):
        return ""
    with io.open(path, encoding="utf-8") as fh:
        return fh.read()


# ---------------------------------------------------------------- 経路
print("[経路]")
player = Character("エリス")
app = App(player)
module, ctx = fresh_mod(app)
for target in TARGETS:
    check("registered " + target, target in ctx.hooks)
check("no extra targets", set(ctx.hooks) == set(TARGETS),
      sorted(set(ctx.hooks) - set(TARGETS)))

# ---------------------------------------------------------------- 差分
print("[差分]")
calls = []


def fake_consume(self, item_instance, usable):
    """本体の代わり。HP を戻し、スタミナを払い、品を減らし、文を足す。"""
    calls.append((item_instance, usable))
    owner = item_instance.obtainer
    owner.current_hp += item_instance.attributes["回復"]
    owner.physical_integrity -= item_instance.attributes["疲労負荷"]
    owner.inventory.pop(next(iter(owner.inventory)))
    self.app.add_text("{}を使った。".format(item_instance.name))
    return "done"


manager = Manager(app)
item = Item("薬草茶", player)
hook = ctx.hooks["__main__:ItemConsumeManager.consume_item"]
result = hook(fake_consume, manager, item, True)
check("result passes through", result == "done", result)
check("original called once with the same args",
      calls == [(item, True)], calls)
rows = [r for r in read_records() if r.get("phase") == "consume_item"]
check("one consume row", len(rows) == 1, len(rows))
row = rows[0] if rows else {}
check("usable recorded", row.get("usable") is True, row.get("usable"))
check("item attributes recorded",
      (row.get("item") or {}).get("attributes", {}).get("回復") == 20, row.get("item"))
pdiff = row.get("player_diff") or {}
check("hp diff", pdiff.get("current_hp") == [100, 120], pdiff)
check("stamina diff", pdiff.get("physical_integrity") == [10, 0], pdiff)
check("inventory diff", pdiff.get("inventory") == [3, 2], pdiff)
check("unchanged fields are not in the diff", "max_hp" not in pdiff, pdiff)
check("owner is the player -> no owner diff", row.get("owner_diff") is None, row)
check("text captured", row.get("texts") == ["薬草茶を使った。"], row.get("texts"))
check("game text still shown", app.texts == ["薬草茶を使った。"], app.texts)

# 持ち主がプレイヤーでない品
mate = Character("ロイ", hp=40, stamina=8, items=1)
item2 = Item("焼き魚", mate, heal=5, burden=2)
hook(fake_consume, manager, item2, False)
rows = [r for r in read_records() if r.get("phase") == "consume_item"]
row = rows[-1] if rows else {}
check("second row usable=False", row.get("usable") is False, row.get("usable"))
check("owner diff recorded separately",
      (row.get("owner_diff") or {}).get("current_hp") == [40, 45], row.get("owner_diff"))
check("player untouched", row.get("player_diff") == {}, row.get("player_diff"))

# 使用の外の add_text は溜めない
add_text = ctx.hooks["__main__:InstantaleApp.add_text"]
add_text(App.add_text, app, "外の文")
check("text outside a use is not buffered", "外の文" not in read_log())
check("text outside a use still shown", app.texts[-1] == "外の文", app.texts)

# ---------------------------------------------------------------- popup
print("[popup]")


class Popup(object):
    def __init__(self, item):
        self.item = item
        self.usable = False
        self.title = "薬草茶"
        self.widget = object()
        self._private = 1


def fake_press(self, instance):
    return "pressed"


popup = Popup(item)
press = ctx.hooks["scripts.hud.new_hud:ItemPopupMenu.on_consume_item"]
check("popup press passes through", press(fake_press, popup, None) == "pressed")
rows = [r for r in read_records() if r.get("phase") == "popup/on_consume_item"]
check("popup row", len(rows) == 1, len(rows))
fields = (rows[0] if rows else {}).get("popup") or {}
check("popup scalar fields inline",
      fields.get("usable") is False and fields.get("title") == "薬草茶", fields)
check("popup object fields by type only", fields.get("widget") == "<object>", fields)
check("popup private fields skipped", "_private" not in fields, fields)
check("popup item recorded", (rows[0].get("item") or {}).get("name") == "薬草茶")

# ---------------------------------------------------------------- 純関数
print("[純関数]")
pure = ctx.hooks["scripts.functions:get_heal_spec"]


def fake_spec(value):
    return value * 3


check("pure result passes through", pure(fake_spec, 3) == 9)
pure(fake_spec, 3)
pure(fake_spec, 4)
rows = [r for r in read_records() if r.get("phase") == "純関数"]
check("same args written once", [r["args"] for r in rows] == [[3], [4]],
      [r["args"] for r in rows])

# ---------------------------------------------------------------- 壊れても
print("[壊れても]")
module2, ctx2 = fresh_mod(app)
module2.snapshot = lambda character: (_ for _ in ()).throw(RuntimeError("boom"))
hook2 = ctx2.hooks["__main__:ItemConsumeManager.consume_item"]
calls[:] = []
result = hook2(fake_consume, manager, Item("壊れ薬", player), True)
check("broken recorder: original still called", len(calls) == 1, calls)
check("broken recorder: result unchanged", result == "done", result)
check("broken recorder: error logged", len(ctx2.errors) >= 1, ctx2.errors)

# ---------------------------------------------------------------- 上限
print("[上限]")
module3, ctx3 = fresh_mod(app)
module3.UPDATE_SAMPLES = 2
update = ctx3.hooks["scripts.characters:Character.update_max_hp"]


def fake_update(self):
    self.max_hp += 1
    return None


for _ in range(5):
    update(fake_update, player)
log = read_log()
check("cap updates outside a use are capped",
      log.count("Character.update_max_hp") == 2, log.count("Character.update_max_hp"))
check("original still runs past the cap", player.max_hp == 125, player.max_hp)

# ---------------------------------------------------------------- 結果
print()
if failures:
    print("FAILED: {}".format(failures))
    sys.exit(1)
print("all ok")
