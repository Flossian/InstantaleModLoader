# -*- coding: utf-8 -*-
"""ローダの窓口 `save_lookup` と `146_fix_save_only_entities` をゲーム抜きで通す。

    python tools/tests/test_save_only_entities.py

確認するもの:

  窓口     … `in_world` はキーの並びで世界の辞書をたどる。読めなければ None。差し替えは入れ子にでき、
             最後の1つが抜けたときに戻す。間に辞書が入れ替わったら戻さない
  守り     … 差し替えの間に `world_data*` へセーブ側の辞書が渡されたら本来の世界の辞書に取り替える。
             `savedata*` や差し替えていないときは触らない
  名乗り   … `cover` した場面は `covered` が持ち主を返す。用済みの ctx は数えない
  146      … 既定は切で、切っていれば包まず名乗らない。入れると、売買・人物の詳細・闘技場の入口で、対象が世界側に無いときだけ `orig` の間セーブ側の辞書が見える。
             世界側にあればゲームのまま。終われば元の辞書に戻る
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
OUT_DIR = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir, "out", "test", "save_only_entities"))

if RUNTIME_DIR not in sys.path:
    sys.path.insert(0, RUNTIME_DIR)

import instantale_modloader as ml                      # noqa: E402
from instantale_modloader import save_lookup, ui       # noqa: E402

failures = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name
          + ((" -- " + str(detail)) if detail and not cond else ""))
    if not cond:
        failures.append(name)


class FakeCtx:
    _mod = "146_fix_save_only_entities"

    def __init__(self, out_dir, alive=True):
        self.out_dir = out_dir
        self.hooks = {}
        self.errors = []
        self.alive = alive

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
        return not self.alive

    def wrap(self, target, **kw):
        def decorator(func):
            self.hooks[target] = func
            return func
        return decorator


# ---------------------------------------------------------------- 偽のゲーム
class Node:
    def __init__(self, node_id, facilities):
        self.id = node_id
        self.facilities = {f.id: f for f in facilities}


class Area:
    def __init__(self, area_id, nodes):
        self.id = area_id
        self.name = "テストの街"
        self.nodes = {n.id: n for n in nodes}


def make_app(world_has_shop=True, world_has_owner=True):
    shop = types.SimpleNamespace(id="308", name="奴隷市場", facility_type="general_store", owner="120")
    area = Area("0", [Node("0", [shop])])
    world = {"areas": {"0": {"nodes": {"0": {"facilities": {}}}}}, "npcs": {}, "world_data": {"name": "w"}}
    save = {"areas": {"0": {"nodes": {"0": {"facilities": {"308": {"config": {}}}}}}},
            "npcs": {"120": {"name": "テスター"}}, "world_data": {"name": "w"}, "player_data": {}}
    if world_has_shop:
        world["areas"]["0"]["nodes"]["0"]["facilities"]["308"] = {"config": {}}
    if world_has_owner:
        world["npcs"]["120"] = {"name": "テスター"}
    app = types.SimpleNamespace(world=types.SimpleNamespace(name="w", areas={"0": area}, characters={}),
                                player=types.SimpleNamespace(current_area="0", location=shop))
    app.world_dict = world
    app.save_data_dict = save
    return app, world, save


shutil.rmtree(OUT_DIR, ignore_errors=True)
os.makedirs(OUT_DIR, exist_ok=True)

print("窓口: in_world")
save_lookup.reset()
app, world, save = make_app(world_has_shop=False)
check("ある段は True", save_lookup.in_world(app, "npcs", "120") is True)
check("無い段は False", save_lookup.in_world(app, "areas", "0", "nodes", "0", "facilities", "308") is False)
check("辞書が無ければ None", save_lookup.in_world(types.SimpleNamespace(), "npcs") is None)

print("窓口: 差し替え")
lines = []
with save_lookup.redirected(app, "test", lines.append) as on:
    check("差し替えた", on and app.world_dict is save)
    check("in_world は本来の世界を見る", save_lookup.in_world(app, "areas", "0", "nodes", "0", "facilities", "308") is False)
    with save_lookup.redirected(app, "nested", lines.append):
        check("入れ子でもセーブ側", app.world_dict is save)
    check("内側が抜けてもセーブ側のまま", app.world_dict is save)
check("最後が抜けたら戻る", app.world_dict is world and not save_lookup.redirecting())
other = {"areas": {}}
with save_lookup.redirected(app, "load", lines.append):
    app.world_dict = other            # 間に読み込みが入った
check("間に入れ替わったら戻さない", app.world_dict is other, app.world_dict)
check("その記録", any("left as is" in line for line in lines), lines)
app.world_dict = world

print("窓口: 守り")
with save_lookup.redirected(app, "guard"):
    check("世界データへのセーブ側の辞書は取り替える", save_lookup.guard("C:/x/worlds/w/world_data.json.temp", save) is world)
    check("セーブへの書き込みは触らない", save_lookup.guard("C:/x/saves/w/savedata.json.temp", save) is save)
    check("別の辞書は触らない", save_lookup.guard("C:/x/worlds/w/world_data.json.temp", world) is world)
check("差し替えていないときは触らない", save_lookup.guard("C:/x/worlds/w/world_data.json", save) is save)

print("窓口: 名乗り")
save_lookup.reset()
check("誰も名乗っていない", save_lookup.covered("shopping") is None)
dead = FakeCtx(OUT_DIR, alive=False)
save_lookup.cover("old", dead, "shopping")
check("用済みの ctx は数えない", save_lookup.covered("shopping") is None)

print("146")
save_lookup.reset()
folder = os.path.join(MODS_DIR, "146_fix_save_only_entities")
with io.open(os.path.join(folder, "mod.json"), encoding="utf-8") as fh:
    entry = json.load(fh)["entry"]
spec = importlib.util.spec_from_file_location("fix_save_only_entities_mod", os.path.join(folder, entry))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
check("既定は切", module.ENABLED is False)
ctx = FakeCtx(OUT_DIR)
module.apply(ctx)
check("切っていれば包まない", not ctx.hooks, list(ctx.hooks))
check("切っていれば名乗らない", all(save_lookup.covered(s) is None for s in module.SITES))
module.ENABLED = True
ctx = FakeCtx(OUT_DIR)
module.apply(ctx)
check("3つの場面を名乗る", all(save_lookup.covered(s) == module.OWNER for s in module.SITES))

saved_find = ui.find_app
try:
    for has_shop, has_owner, expect_save, label in ((True, True, False, "世界側にある店はゲームのまま"),
                                                    (False, True, True, "世界側に無い店はセーブ側"),
                                                    (True, False, True, "主が世界側に無い店もセーブ側")):
        app, world, save = make_app(world_has_shop=has_shop, world_has_owner=has_owner)
        ui.find_app = lambda app=app: app
        manager = types.SimpleNamespace(app=app)
        seen = []
        ctx.hooks[module.SHOP_TARGET](lambda self: seen.append(app.world_dict), manager)
        check("売買: " + label, seen and (seen[0] is save) == expect_save, (seen, label))
        check("売買: 終われば元に戻る（{}）".format(label), app.world_dict is world)

        manager = types.SimpleNamespace(app=app)
        seen = []
        ctx.hooks[module.COLOSSEUM_TARGET](lambda self, enemy_id: seen.append(app.world_dict), manager, "3")
        check("闘技場: " + label, seen and (seen[0] is save) == expect_save, (seen, label))
        seen = []
        ctx.hooks[module.COLOSSEUM_METHOD_TARGET](lambda self: seen.append(app.world_dict), manager)
        check("闘技場の仕込み: " + label, seen and (seen[0] is save) == expect_save, (seen, label))

    for in_world, expect_save, label in ((True, False, "世界側にいる人物はゲームのまま（詳細は世界側にも書く）"),
                                         (False, True, "世界側にいない人物はセーブ側")):
        app, world, save = make_app(world_has_owner=in_world)
        character = types.SimpleNamespace(id="120", name="テスター")
        seen = []
        ctx.hooks[module.NPC_DETAIL_TARGET](lambda self, c: seen.append(app.world_dict), app, character)
        check("人物の詳細: " + label, seen and (seen[0] is save) == expect_save, (seen, label))
        check("人物の詳細: 終われば元に戻る（{}）".format(label), app.world_dict is world)

    print("146: 保存の守り")
    app, world, save = make_app(world_has_shop=False)
    ui.find_app = lambda: app
    written = []
    writer = ctx.hooks[save_lookup.WRITE_TARGET]

    def orig_shop(self):
        # 差し替えの間に保存が走った（ゲームの save_game は self.world_dict を世界データへ書く）
        writer(lambda path, data: written.append((path, data)), "C:/x/worlds/w/world_data.json.temp", app.world_dict)
        writer(lambda path, data: written.append((path, data)), "C:/x/saves/w/savedata.json.temp", app.save_data_dict)

    ctx.hooks[module.SHOP_TARGET](orig_shop, types.SimpleNamespace(app=app))
    check("世界データには本来の世界の辞書を書く", written and written[0][1] is world, written)
    check("セーブにはセーブの辞書を書く", len(written) > 1 and written[1][1] is save, written)
    check("例外を出していない", not ctx.errors, ctx.errors)
finally:
    ui.find_app = saved_find
    save_lookup.reset()

if failures:
    print("FAILED: " + ", ".join(failures))
    sys.exit(1)
print("OK")
