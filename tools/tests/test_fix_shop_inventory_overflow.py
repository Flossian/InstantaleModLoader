# -*- coding: utf-8 -*-
"""108_fix_shop_inventory_overflow をゲーム抜きで通す。

    python tools/tests/test_fix_shop_inventory_overflow.py

確認するもの:

  救済   … `place_existing_item` が IndexError で落ちたら、ゲーム自身の `place_new_item` に流す
  諦め   … `place_new_item` も落ちたら、その品だけ置かずに先へ進む（例外を通さない）
  見本   … 正常に置けた回は、グリッドの種類ごとに1プロセス1件だけ書く（ログが世代送りされたら新しいログにも書く）。
           遅れて当て直す boot と注入し直しで数え直さない（版2。版1は apply ごとに30件まで書いていた）
  時刻   … 行に時刻が付く（`ctx.logger` の既定）
  記録   … はみ出し（OVERFLOW）と諦め（GIVEUP）は毎回書く
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
OUT_DIR = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir, "out", "test",
                                        "fix_shop_inventory_overflow"))

if RUNTIME_DIR not in sys.path:
    sys.path.insert(0, RUNTIME_DIR)

import instantale_modloader as ml                      # noqa: E402

failures = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name
          + ((" -- " + str(detail)) if detail and not cond else ""))
    if not cond:
        failures.append(name)


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


MOD_DIR, MOD = find_mod("_fix_shop_inventory_overflow")

# 見本の控えの置き場（mod の `SAMPLE_STORE_ATTR`）。mod を読む前に捨てるので名前で持つ。
SAMPLE_STORE_ATTR = "_instantale_shop_inventory_samples"


# ---------------------------------------------------------------- 偽ゲーム
class Item:
    def __init__(self, item_id, height=1):
        self.item_id = item_id
        self.width_slots = 1
        self.height_slots = height
        self.pos = [0, 0]
        self.size = [64, 64]
        self.current_slots = []

    def clear_current_slots(self):
        self.current_slots = []


class InventoryGrid:
    """`scripts.hud.new_hud.InventoryGrid` の、置き場所に関わるところだけ。"""

    def __init__(self, situation, cols=4, rows=6, overflow=False, full=False):
        self.situation = situation
        self.cols = cols
        self.rows = rows
        self.spacing = [1, 1]
        self.size = [259, 389]
        self.pos = [0, 0]
        self.slots = [None] * (cols * rows)
        self.overflow = overflow
        self.full = full
        self.placed_new = []

    def place_existing_item(self, item):
        if self.overflow:
            raise IndexError("list index out of range")
        item.current_slots = [0]
        return "placed"

    def find_placement_position(self, w, h):
        return (0, 0)

    def place_new_item(self, item):
        if self.full:
            raise RuntimeError("no room")
        self.placed_new.append(item)
        return "placed anew"

    def is_valid_placement(self, *args):
        return True


_PRISTINE = InventoryGrid.place_existing_item


class FakeCtx:
    def __init__(self, out_dir):
        self.out_dir = out_dir
        self.mod_dir = MOD_DIR
        self.logs = []
        self.errors = []

    def out_path(self, *parts):
        path = os.path.join(self.out_dir, *parts)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        return path

    # ログは本物の `ctx.logger` をそのまま借りる。
    _mod = None

    def logger(self, name, **kw):
        return ml.ModContext.logger(self, name, **kw)

    def log(self, msg, level="INFO"):
        self.logs.append((level, msg))

    def log_exc(self, msg):
        import traceback
        self.errors.append(msg + "\n" + traceback.format_exc())

    def wrap(self, target, **kw):
        name = target.rsplit(".", 1)[-1]

        def decorate(fn):
            orig = _PRISTINE

            def wrapper(self_, *args, **kwargs):
                return fn(orig, self_, *args, **kwargs)

            setattr(InventoryGrid, name, wrapper)
            return fn
        return decorate


def install_fake_game():
    module = types.ModuleType("scripts.hud.new_hud")
    module.InventoryGrid = InventoryGrid
    sys.modules["scripts.hud.new_hud"] = module


def apply_mod():
    """mod を読み直して当てる（遅れて当て直す boot と同じく、モジュールは毎回読み直される）。"""
    spec = importlib.util.spec_from_file_location(
        "fix_shop_inventory_overflow_mod", MOD,
        submodule_search_locations=[os.path.dirname(MOD)])
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    ctx = FakeCtx(OUT_DIR)
    module.apply(ctx)
    return module, ctx


def read_log():
    path = os.path.join(OUT_DIR, "inventory.log")
    if not os.path.isfile(path):
        return []
    with io.open(path, encoding="utf-8") as fh:
        return fh.read().splitlines()


def main():
    print("== 108_fix_shop_inventory_overflow ==")
    shutil.rmtree(OUT_DIR, ignore_errors=True)
    install_fake_game()
    try:
        if hasattr(sys, SAMPLE_STORE_ATTR):
            delattr(sys, SAMPLE_STORE_ATTR)           # 新しいプロセスから始める
        module, ctx = apply_mod()

        # -- 見本 -------------------------------------------------------------
        shop, loot = InventoryGrid("shop"), InventoryGrid("loot")
        for _ in range(40):
            shop.place_existing_item(Item("item_1"))
        lines = read_log()
        ok = [line for line in lines if "] ok " in line]
        check("見本: 同じ種類のグリッドは1件だけ", len(ok) == 1, lines)
        check("見本: 行に時刻が付く", bool(ok) and ok[0].startswith("[20"), ok)

        loot.place_existing_item(Item("item_2"))
        ok = [line for line in read_log() if "] ok " in line]
        check("見本: 種類が違えば書く", len(ok) == 2 and "situation='loot'" in ok[-1], ok)

        # 遅れて当て直す boot（モジュールも読み直される）。見本は数え直さない。
        module, ctx = apply_mod()
        for _ in range(5):
            InventoryGrid("shop").place_existing_item(Item("item_3"))
        ok = [line for line in read_log() if "] ok " in line]
        check("見本: 当て直しても数え直さない", len(ok) == 2, ok)

        # 上限。種類が多くても SAMPLE_OK 件で止まる。
        for index in range(module.SAMPLE_OK + 5):
            InventoryGrid("kind{}".format(index)).place_existing_item(Item("item_4"))
        ok = [line for line in read_log() if "] ok " in line]
        check("見本: 1プロセスで SAMPLE_OK 件まで", len(ok) == module.SAMPLE_OK, len(ok))

        # 注入のときにログが世代送りされた形。新しいログにはもう一度見本を書く。
        log_path = os.path.join(OUT_DIR, "inventory.log")
        os.replace(log_path, log_path + ".1")
        module, ctx = apply_mod()
        InventoryGrid("shop").place_existing_item(Item("item_5"))
        ok = [line for line in read_log() if "] ok " in line]
        check("見本: ログが入れ替わったら新しいログに書く",
              len(ok) == 1 and "situation='shop'" in ok[0], ok)
        module, ctx = apply_mod()                   # 入れ替わっていなければ数え直さない
        InventoryGrid("shop").place_existing_item(Item("item_6"))
        ok = [line for line in read_log() if "] ok " in line]
        check("見本: 同じログなら当て直しても書かない", len(ok) == 1, ok)

        # -- 救済と記録 -------------------------------------------------------
        grid = InventoryGrid("shop", overflow=True)
        tall = Item("item_tall", height=3)
        for _ in range(2):
            result = grid.place_existing_item(tall)
        check("救済: はみ出したらゲーム自身の配置に流す",
              result == "placed anew" and grid.placed_new == [tall, tall], result)
        overflow = [line for line in read_log() if "] OVERFLOW " in line]
        check("記録: はみ出しは毎回書く", len(overflow) == 2, overflow)

        grid = InventoryGrid("shop", overflow=True, full=True)
        result = grid.place_existing_item(Item("item_lost"))
        giveup = [line for line in read_log() if "] GIVEUP " in line]
        check("諦め: 置けなければその品だけ置かずに進む", result is None, result)
        check("記録: 諦めは書く", len(giveup) == 1, giveup)
        check("例外を握り潰していない", not ctx.errors, ctx.errors)
    finally:
        InventoryGrid.place_existing_item = _PRISTINE
        sys.modules.pop("scripts.hud.new_hud", None)
        if hasattr(sys, SAMPLE_STORE_ATTR):
            delattr(sys, SAMPLE_STORE_ATTR)

    print("")
    if failures:
        print("失敗: {}".format(", ".join(failures)))
        return 1
    print("すべて通った")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
