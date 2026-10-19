# -*- coding: utf-8 -*-
"""409_fix_crafted_item_type をゲーム抜きで通す。

    python tools/tests/test_fix_crafted_item_type.py

確認するもの:

  直す       … consumable の飲食物・薬（6つの細分）を healing_item にする。巻物・ほかの種別は触らない
  鍵を足す   … 回復も疲労負荷も無い品に本体の関数の値を足す。並びは item_detail → 回復 → 疲労負荷 → 残り
  足さない   … 134_ が作り直した品（疲労負荷 0 が在る）・本体の関数が引けないとき
  英語       … 表示言語が en なら recovery / fatigue の綴りで足す
  入口       … 生成3本・クラフトの置き場・窓2種（持ち主でも入れ物でも）・説明欄。引数は素通し
  134_ と    … 足した品を 134_ が作り直すと、134_ の効き目だけが残る
  設定       … FIX_ON_SIGHT を切ると画面の入口は包まない
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
OUT_DIR = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir, "out", "test",
                                        "fix_crafted_item_type"))

if RUNTIME_DIR not in sys.path:
    sys.path.insert(0, RUNTIME_DIR)

import instantale_modloader as ml                      # noqa: E402


def find_mod(suffix):
    matches = sorted(name for name in os.listdir(MODS_DIR)
                     if name.endswith(suffix)
                     and os.path.isfile(os.path.join(MODS_DIR, name, "mod.json")))
    if len(matches) != 1:
        raise SystemExit("cannot find one *{} in {}: {}".format(suffix, MODS_DIR, matches))
    folder = os.path.join(MODS_DIR, matches[0])
    with io.open(os.path.join(folder, "mod.json"), encoding="utf-8") as fh:
        entry = json.load(fh)["entry"]
    return folder, os.path.join(folder, entry)


failures = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name
          + ((" -- " + str(detail)) if detail and not cond else ""))
    if not cond:
        failures.append(name)


class Item(object):
    def __init__(self, name, item_type, attributes, value=10):
        self.name = name
        self.item_type = item_type
        self.attributes = attributes
        self.value = value
        self.rarity = "common"


class Widget(object):
    """画面の部品（`InventoryItem`）。"""

    def __init__(self, item):
        self.item_instance = item


class Container(object):
    """`ItemContainer`。`.inventory` が `{鍵: Item}`。"""

    def __init__(self, entries):
        self.inventory = entries


class Character(object):
    def __init__(self, entries):
        self.inventory = Container(entries)


class FakeCtx:
    _mod = "409_fix_crafted_item_type"

    def __init__(self, out_dir):
        self.out_dir = out_dir
        self.hooks = {}
        self.errors = []
        self.logs = []

    def out_path(self, *parts):
        path = os.path.join(self.out_dir, *parts)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        return path

    def logger(self, name, **kw):
        return ml.ModContext.logger(self, name, **kw)

    def log(self, msg, level="INFO"):
        self.logs.append(msg)

    def log_exc(self, msg):
        self.errors.append(msg)

    def wrap(self, target, **kw):
        def decorator(func):
            self.hooks[target] = func
            return func
        return decorator


def heal_spec(value):
    return value * 3


def heal_burden(value):
    return 10


def install_game(lang="ja", functions=True):
    sys.modules["scripts.languages"] = types.SimpleNamespace(language=lang)
    if functions:
        sys.modules["scripts.functions"] = types.SimpleNamespace(
            get_heal_spec=heal_spec, get_heal_physical_integrity_barden=heal_burden)
    else:
        sys.modules.pop("scripts.functions", None)


def load(name, folder, path):
    sys.modules.pop(name, None)
    spec = importlib.util.spec_from_file_location(
        name, path, submodule_search_locations=[folder])
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def fresh(on_sight=True):
    if hasattr(sys, "_instantale_crafted_item_type_store"):
        delattr(sys, "_instantale_crafted_item_type_store")
    module = load("fix_crafted_item_type_mod", *find_mod("_fix_crafted_item_type"))
    module.FIX_ON_SIGHT = on_sight
    ctx = FakeCtx(OUT_DIR)
    module.apply(ctx)
    return module, ctx


def food(detail="food", **extra):
    attributes = {"item_detail": detail}
    attributes.update(extra)
    attributes.setdefault("売価", 12)
    return Item("干し肉", "consumable", attributes, value=7)


saved = {name: sys.modules.get(name) for name in ("scripts.languages", "scripts.functions")}
try:
    install_game()
    mod, ctx = fresh()

    print("直す")
    for detail in ("food", "drink", "medicine", "potion", "plant", "mushroom"):
        item = food(detail)
        check("{} を直す".format(detail), mod.fix(item) is not None
              and item.item_type == "healing_item", item.item_type)
    scroll = food("scroll")
    check("巻物は触らない", mod.fix(scroll) is None and scroll.item_type == "consumable")
    tool = Item("縄", "utility", {"item_detail": "food"})
    check("consumable 以外は触らない", mod.fix(tool) is None and tool.item_type == "utility")
    healing = Item("薬草", "healing_item", {"item_detail": "plant", "回復": 9, "疲労負荷": 10})
    check("既に healing_item なら何もしない", mod.fix(healing) is None)
    check("attributes が辞書でなければ触らない",
          mod.fix(Item("x", "consumable", None)) is None)
    record = {"name": "葡萄酒", "item_type": "consumable", "value": 5,
              "attributes": {"item_detail": "drink", "買価": 30}}
    check("セーブの辞書も直す", mod.fix(record) == (15, 10)
          and record["item_type"] == "healing_item", record)
    widget = Widget(food())
    check("画面の部品は中の品を直す", mod.fix(widget) is not None
          and widget.item_instance.item_type == "healing_item")

    print("鍵を足す")
    item = food()
    check("足した値を返す", mod.fix(item) == (21, 10))
    check("並びは item_detail → 回復 → 疲労負荷 → 残り",
          list(item.attributes.items())
          == [("item_detail", "food"), ("回復", 21), ("疲労負荷", 10), ("売価", 12)],
          item.attributes)
    held = item.attributes
    check("辞書は同じもの", food() is not None and item.attributes is held)

    print("足さない")
    reworked = food(**{"スタミナ回復": 14, "疲労負荷": 0})
    check("134_ が作り直した品には足さない", mod.fix(reworked) is False
          and "回復" not in reworked.attributes
          and reworked.item_type == "healing_item", reworked.attributes)
    install_game(functions=False)
    bare = food()
    check("関数が引けなければ種別だけ直す", mod.fix(bare) is False
          and bare.item_type == "healing_item" and "回復" not in bare.attributes)
    install_game()
    odd = Item("謎の汁", "consumable", {"item_detail": "drink"}, value="7")
    check("value が数でなければ足さない", mod.fix(odd) is False)

    print("英語")
    install_game(lang="en")
    item = food()
    mod.fix(item)
    check("recovery / fatigue で足す",
          list(item.attributes)[:3] == ["item_detail", "recovery", "fatigue"], item.attributes)
    install_game()

    print("入口")
    mod, ctx = fresh()
    names = ("__main__:InstantaleApp.generate_item_from_item_data",
             "__main__:InstantaleApp.generate_item_from_dict",
             "__main__:InstantaleApp.generate_item_from_ready_made_data")
    for target in names:
        made = food()
        result = ctx.hooks[target](lambda self, *a, **k: made, object(), "x", obtainer=None)
        check(target.split(".")[-1] + " の戻り値を直す",
              result is made and made.item_type == "healing_item")

    seen = []

    def place(self, generated_item, generated_item_id):
        seen.append((generated_item.item_type, generated_item_id))
        return "placed"

    hook = ctx.hooks["scripts.hud.new_hud:InstanTaleHUD.place_crafted_item"]
    check("クラフトの置き場（位置）", hook(place, object(), food(), "item_9") == "placed"
          and seen[-1] == ("healing_item", "item_9"), seen)
    check("クラフトの置き場（名前）",
          hook(place, object(), generated_item=food(), generated_item_id="item_10") == "placed"
          and seen[-1] == ("healing_item", "item_10"), seen)

    left_item, right_item = food(), food("potion")
    passed = []

    def twin(self, left_inventory_obtainer, right_inventory_obtainer,
             left_label_text, situation):
        passed.append((left_label_text, situation))
        return "opened"

    hook = ctx.hooks["__main__:InstantaleApp.toggle_twin_inventory_window"]
    result = hook(twin, object(), Character({"a": left_item}), Container({"b": right_item}),
                  "店", situation="shop")
    check("二つ窓: 持ち主と入れ物の両方を直す", left_item.item_type == "healing_item"
          and right_item.item_type == "healing_item")
    check("二つ窓: 引数は素通し", result == "opened" and passed == [("店", "shop")], passed)
    hook(twin, object(), left_inventory_obtainer=None, right_inventory_obtainer=None,
         left_label_text="x", situation=None)
    check("二つ窓: 名前で渡されても落ちない", passed[-1] == ("x", None) and not ctx.errors)

    app = types.SimpleNamespace(player=Character({"c": food("medicine")}))
    hook = ctx.hooks["__main__:InstantaleApp.toggle_center_inventory_window"]
    hook(lambda self, *a: "center", app)
    check("中央の窓: 主人公の持ち物を直す",
          app.player.inventory.inventory["c"].item_type == "healing_item")

    shown = []
    hook = ctx.hooks["scripts.hud.new_hud:ItemDetailBox.update_content"]
    hook(lambda self, item: shown.append(item.item_type), object(), food("drink"))
    check("説明欄: 描く前に直す", shown == ["healing_item"], shown)
    hook(lambda self, item: shown.append(item.item_type), object(), item=food("drink"))
    check("説明欄: 名前で渡されても直す", shown[-1] == "healing_item", shown)

    log_path = os.path.join(OUT_DIR, "crafted_item_type.log")
    with io.open(log_path, encoding="utf-8") as fh:
        text = fh.read()
    check("ログに行が出る", "consumable -> healing_item (+heal 21/10)" in text, text[-300:])

    print("134_ と")
    effects_dir, effects_path = find_mod("_balance_item_effects")
    effects = load("item_effects_for_409", effects_dir, effects_path)
    item = food("medicine")
    mod.fix(item)
    effects.rework(item)
    check("134_ が作り直すと回復は割合・疲労負荷は 0",
          str(item.attributes.get("回復", "")).endswith("%")
          and item.attributes.get("疲労負荷") == 0, item.attributes)
    item = food("food")
    mod.fix(item)
    effects.rework(item)
    check("食料は 回復 の鍵が消え スタミナ回復 だけ",
          "回復" not in item.attributes and "スタミナ回復" in item.attributes, item.attributes)

    print("設定")
    mod, ctx = fresh(on_sight=False)
    check("FIX_ON_SIGHT を切ると画面の入口は包まない",
          "__main__:InstantaleApp.toggle_twin_inventory_window" not in ctx.hooks
          and "scripts.hud.new_hud:ItemDetailBox.update_content" not in ctx.hooks
          and "__main__:InstantaleApp.generate_item_from_item_data" in ctx.hooks,
          list(ctx.hooks))
finally:
    for name, module in saved.items():
        if module is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = module

if failures:
    print("FAILED: " + ", ".join(failures))
    sys.exit(1)
print("OK")
