# -*- coding: utf-8 -*-
"""906_fix_new_shop_data をゲーム抜きで通す。

    python tools/tests/test_wip_new_shop_data.py

偽の `ShoppingStartManagerRemake` を本番と同じ形で組む。
**`self` 以外の引数を取らず**、id は自分の中で求めてから `world_dict` 側を引く
（引数から id を拾おうとした版1・版2が実機で空振りした形。この MOD の `DOC.md` §3）。
配布物に入れない MOD（9xx）なので CI では走らない（TECH.md §2.6）。

  施設      … `KeyError` の施設 id を写して、やり直しで売買が開く
  主        … 主だけが欠けているときは NPC を1人写す
  連鎖      … 施設と主が続けて欠けていても、やり直しを重ねて開く
  ノード    … ノードごと欠けているとき、ノードが丸ごと写る
  エリア    … エリアごと欠けているとき、エリアが丸ごと写る
  並び      … 写した NPC の項目の並びが供給元のまま（`speech_style` を欠く NPC も素のまま）
  独立      … 写した後に供給元を書き換えても、写した側は動かない（deepcopy）
  冪等      … 2度目の来店は1回で開き、記録が増えない
  無い      … どちらの辞書にも無ければ素の `KeyError` がそのまま出る
  堂々巡り  … 埋めたのに同じキーで落ちるときは、やり直しを繰り返さず記録して降りる
  形違い    … 鍵は在るのに中身が辞書でない段は上書きしない
  1つだけ   … 素データの辞書が1つしかない版では何もしない
  他の例外  … `KeyError` 以外で落ちても記録は残す
  無事故    … どの経路でも `ctx.log_exc` が呼ばれない

素データが2つあって片方に追加が届かないことの実測は GAME.md §2.28。
"""
import importlib.util
import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
RUNTIME_DIR = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir, "runtime"))
MODS_DIR = os.path.join(RUNTIME_DIR, "mods")
OUT_DIR = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir, "out", "test"))

try:
    sys.stdout.reconfigure(errors="replace")
except Exception:
    pass

if RUNTIME_DIR not in sys.path:
    sys.path.insert(0, RUNTIME_DIR)


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


MOD = find_mod("_fix_new_shop_data")
TARGET = "__main__:ShoppingStartManagerRemake.shopping_start_method_1"
LOG_NAME = "new_shop_data.log"

failures = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name
          + ((" -- " + str(detail)) if detail and not cond else ""))
    if not cond:
        failures.append(name)


# ---------------------------------------------------------------- 偽の素データ
# 実セーブ（ヴェスティア）の形を縮めたもの。
# 施設は8項目、NPC は33項目で、どちらもゲーム自身が欠ける個体を持っている
# （施設は `tier` を欠くものが18件、遊んでいる最中に生まれた NPC 8人は
# `speech_style` を欠く）。その不揃いも再現してある。
NPC_KEYS = ("name", "id", "category", "profile", "personality",
            "look_description", "speech_style", "job", "state", "config")


def facility(fid, name, owner, tier="basic"):
    values = {"name": name, "id": fid, "description": "説明",
              "facility_type": "general_store", "tier": tier, "owner": owner,
              "connections": [], "config": {"level_of_detail": 0}}
    if tier is None:
        del values["tier"]
    return values


def npc(nid, name, speech_style="丁寧"):
    """NPC の素データ。`speech_style=None` で、その項目を持たない個体になる。"""
    values = {"name": name, "id": nid, "category": "npc", "profile": "人物像",
              "personality": "気質", "look_description": "外見",
              "speech_style": speech_style, "job": "other", "state": "",
              "config": {"level_of_detail": 2, "is_player": False,
                         "is_dead": False, "difficulty_level": 4}}
    if speech_style is None:
        del values["speech_style"]
    return {key: values[key] for key in NPC_KEYS if key in values}


def node(nid, facilities):
    return {"name": "ノード" + nid, "id": nid, "overview": "概要",
            "facilities": {item["id"]: item for item in facilities},
            "connections": [], "entrance_facility": facilities[0]["id"],
            "config": {}}


def area(aid, nodes):
    return {"name": "エリア" + aid, "id": aid, "descriptions": {},
            "size": "town", "resident_npcs": [], "adventurer_npcs": [],
            "connections": [], "nodes": {item["id"]: item for item in nodes},
            "quests": {}, "labors": {}, "entrance_node": nodes[0]["id"],
            "bgm": "", "config": {}}


def world_side():
    """世界生成のときからある分だけを持つ辞書（追加が届いていない側）。"""
    return {"areas": {"8": area("8", [node("32", [facility("197", "鍛冶場", "50")])])},
            "npcs": {"50": npc("50", "ドロテア")},
            "index": {"facility": 230, "npc": 100}}


def save_side():
    """遊んだ結果まで入っている辞書。世界側の厳密な上位集合。"""
    data = world_side()
    # 既にあるノードに生えた店（世界側には施設1件だけが足りない）。
    data["areas"]["8"]["nodes"]["32"]["facilities"]["229"] = \
        facility("229", "よろず屋", "99")
    # 同じエリアに生えたノードごと新しい区画。
    data["areas"]["8"]["nodes"]["33"] = node(
        "33", [facility("301", "露店", "101", tier=None)])
    # まるごと新しいエリア。
    data["areas"]["37"] = area("37", [node("37", [facility("300", "旅籠", "100")])])
    # 遊んでいる最中に生まれた NPC は `speech_style` を持たない。
    for nid, name in (("99", "ガルド"), ("100", "エデル"), ("101", "行商人")):
        data["npcs"][nid] = npc(nid, name, speech_style=None)
    return data


# ------------------------------------------------------------------ 偽ゲーム
class ShoppingStartManagerRemake:
    """売買の入口。

    **引数は `self` だけ**（実機で確認）。
    id は自分の中で求めてから `world_dict` 側の素データを引く。
    """

    def __init__(self, app, target="229", route=("8", "32")):
        self.app = app
        self.target = target
        self.route = route
        self.opened = []
        self.calls = 0

    def shopping_start_method_1(self):
        self.calls += 1
        area_id, node_id = self.route
        facility_id = self.target
        table = self.app.world_dict
        shop = table["areas"][area_id]["nodes"][node_id]["facilities"][facility_id]
        owner = table["npcs"][shop["owner"]]
        self.opened.append((shop["name"], owner["name"]))
        return "opened"


class World:
    def __init__(self):
        self.areas = {}


class App:
    def __init__(self, world_dict, save_data_dict=None):
        self.world_dict = world_dict
        if save_data_dict is not None:
            self.save_data_dict = save_data_dict
        self.world = World()
        self.player = None


# ------------------------------------------------------- 偽の ctx とフック
class FakeCtx:
    def __init__(self, out_dir):
        self.out_dir = out_dir
        self.hooks = {}
        self.errors = []
        self.notes = []

    def out_path(self, *parts):
        path = os.path.join(self.out_dir, *parts)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        return path

    # ログは本物の `ctx.logger` をそのまま借りる（検査だけ別の処理を通さない）。
    _mod = None

    def logger(self, name, *, tag=None, stamp=True, label=None, cap=None):
        import instantale_modloader as _ml
        return _ml.ModContext.logger(self, name, tag=tag, stamp=stamp,
                                     label=label, cap=cap)

    def log(self, msg, level="INFO"):
        self.notes.append(msg)

    def log_exc(self, msg):
        self.errors.append(msg)

    def wrap(self, target, **kw):
        def decorator(func):
            self.hooks[target] = func
            return func
        return decorator


def load_mod():
    spec = importlib.util.spec_from_file_location(
        "new_shop_data_mod", MOD,
        submodule_search_locations=[os.path.dirname(MOD)])
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def setup(world=None, save=None, target="229", route=("8", "32")):
    """MOD を読み込んでフックを載せた `(ctx, manager, world, save)`。"""
    path = os.path.join(OUT_DIR, LOG_NAME)
    if os.path.exists(path):
        os.remove(path)
    ctx = FakeCtx(OUT_DIR)
    load_mod().apply(ctx)

    world = world_side() if world is None else world
    save = save_side() if save is None else save
    manager = ShoppingStartManagerRemake(App(world, save), target, route)

    hook = ctx.hooks[TARGET]
    original = ShoppingStartManagerRemake.shopping_start_method_1

    def call(self):
        return hook(original, self)

    manager.shopping_start_method_1 = call.__get__(manager)
    return ctx, manager, world, save


def log_text():
    path = os.path.join(OUT_DIR, LOG_NAME)
    if not os.path.exists(path):
        return ""
    with io.open(path, encoding="utf-8", errors="replace") as fh:
        return fh.read()


def shop_of(table, area_id, node_id, facility_id):
    try:
        return table["areas"][area_id]["nodes"][node_id]["facilities"][facility_id]
    except (KeyError, TypeError):
        return None


# ============================================================== ここから検査
print("-- 施設1件だけが欠けている --")
ctx, manager, world, save = setup()
result = manager.shopping_start_method_1()
check("売買が開く", result == "opened", result)
check("やり直しは1回だけ", manager.calls == 2, manager.calls)
check("施設が世界側にも入った", shop_of(world, "8", "32", "229") is not None)
check("主も一緒に入った", "99" in world["npcs"], sorted(world["npcs"]))
check("店と主の名前が引けている", manager.opened == [("よろず屋", "ガルド")],
      manager.opened)
check("写したことが記録に残る", "copied the facility" in log_text(),
      log_text()[-300:])
check("ローダのログにも1行出る",
      any("filled in '229'" in note for note in ctx.notes), ctx.notes)
check("例外を握り潰していない", ctx.errors == [], ctx.errors)

print("\n-- 主だけが欠けている --")
ctx, manager, world, save = setup()
world["areas"]["8"]["nodes"]["32"]["facilities"]["229"] = \
    save["areas"]["8"]["nodes"]["32"]["facilities"]["229"]
result = manager.shopping_start_method_1()
check("売買が開く", result == "opened", result)
check("NPC だけを写す", "copied npc 99" in log_text(), log_text()[-300:])
check("主が世界側に入った", world["npcs"].get("99", {}).get("name") == "ガルド",
      sorted(world["npcs"]))

print("\n-- 写したものは供給元から独立している --")
ctx, manager, world, save = setup()
manager.shopping_start_method_1()
save["areas"]["8"]["nodes"]["32"]["facilities"]["229"]["name"] = "書き換えた"
save["npcs"]["99"]["name"] = "書き換えた"
check("施設が供給元と繋がっていない",
      shop_of(world, "8", "32", "229")["name"] == "よろず屋",
      shop_of(world, "8", "32", "229")["name"])
check("NPC が供給元と繋がっていない", world["npcs"]["99"]["name"] == "ガルド",
      world["npcs"]["99"]["name"])

print("\n-- 項目の並びは供給元のまま --")
ctx, manager, world, save = setup()
manager.shopping_start_method_1()
check("NPC の項目の並びが変わらない",
      list(world["npcs"]["99"].keys()) == list(save["npcs"]["99"].keys()),
      list(world["npcs"]["99"].keys()))
check("欠けている項目を足していない",
      "speech_style" not in world["npcs"]["99"], sorted(world["npcs"]["99"]))
check("施設の項目の並びが変わらない",
      list(shop_of(world, "8", "32", "229").keys())
      == list(shop_of(save, "8", "32", "229").keys()))

print("\n-- 2度目の来店 --")
ctx, manager, world, save = setup()
manager.shopping_start_method_1()
first = log_text()
manager.calls = 0
result = manager.shopping_start_method_1()
check("1回で開く（やり直さない）", manager.calls == 1, manager.calls)
check("記録が増えない", log_text() == first, log_text()[len(first):][:200])
check("2度目も同じ店", manager.opened[-1] == ("よろず屋", "ガルド"),
      manager.opened)

print("\n-- ノードごと欠けている --")
ctx, manager, world, save = setup(target="301", route=("8", "33"))
result = manager.shopping_start_method_1()
check("売買が開く", result == "opened", result)
check("ノードが丸ごと入った", "33" in world["areas"]["8"]["nodes"],
      sorted(world["areas"]["8"]["nodes"]))
check("中の施設の主も揃った", "101" in world["npcs"], sorted(world["npcs"]))
check("写した段がノードだと記録される", "copied the node" in log_text(),
      log_text()[-300:])
check("`tier` を欠く施設を素のまま写す",
      "tier" not in shop_of(world, "8", "33", "301"),
      sorted(shop_of(world, "8", "33", "301")))

print("\n-- エリアごと欠けている --")
ctx, manager, world, save = setup(target="300", route=("37", "37"))
result = manager.shopping_start_method_1()
check("売買が開く", result == "opened", result)
check("エリアが丸ごと入った", "37" in world["areas"], sorted(world["areas"]))
check("中の施設の主も揃った", "100" in world["npcs"], sorted(world["npcs"]))
check("写した段がエリアだと記録される", "copied the area" in log_text(),
      log_text()[-300:])
check("例外を握り潰していない", ctx.errors == [], ctx.errors)

print("\n-- どちらの辞書にも無い --")
ctx, manager, world, save = setup(target="999")
try:
    manager.shopping_start_method_1()
    raised = None
except KeyError as exc:
    raised = str(exc)
check("素の KeyError がそのまま出る", raised == "'999'", raised)
check("やり直していない", manager.calls == 1, manager.calls)
check("写すものが無いと記録される", "nothing to copy for '999'" in log_text(),
      log_text()[-400:])
check("開けなかったことが記録される", "did not open" in log_text(),
      log_text()[-400:])
check("辞書の中身が書き出される", "census:" in log_text(), log_text()[-400:])
check("組み上がった側の様子も出る", "runtime:" in log_text(), log_text()[-400:])
check("例外を握り潰していない", ctx.errors == [], ctx.errors)

print("\n-- 落ちても記録は1度だけ --")
before = log_text()
for _ in range(3):
    try:
        manager.shopping_start_method_1()
    except KeyError:
        pass
check("繰り返しても記録が増えない", log_text() == before,
      log_text()[len(before):][:200])

print("\n-- 埋めたのに同じキーで落ちる --")


class StubbornManager(ShoppingStartManagerRemake):
    """写しても引けない相手（ゲームが別の場所を引いている場合の再現）。"""

    def shopping_start_method_1(self):
        self.calls += 1
        raise KeyError(self.target)


ctx = FakeCtx(OUT_DIR)
path = os.path.join(OUT_DIR, LOG_NAME)
if os.path.exists(path):
    os.remove(path)
load_mod().apply(ctx)
world, save = world_side(), save_side()
stubborn = StubbornManager(App(world, save))
hook = ctx.hooks[TARGET]
try:
    hook(StubbornManager.shopping_start_method_1, stubborn)
    raised = None
except KeyError as exc:
    raised = str(exc)
check("素の KeyError がそのまま出る", raised == "'229'", raised)
check("やり直しは1回でやめる", stubborn.calls == 2, stubborn.calls)
check("堂々巡りだと分かる記録が出る",
      "still cannot find it" in log_text(), log_text()[-400:])
check("それでも写しはした", shop_of(world, "8", "32", "229") is not None)
check("例外を握り潰していない", ctx.errors == [], ctx.errors)

print("\n-- 鍵は在るのに中身が辞書でない --")
ctx, manager, world, save = setup()
world["areas"]["8"]["nodes"]["32"]["facilities"]["229"] = "壊れた値"
try:
    manager.shopping_start_method_1()
except (KeyError, TypeError):
    pass
check("上書きしない",
      world["areas"]["8"]["nodes"]["32"]["facilities"]["229"] == "壊れた値",
      world["areas"]["8"]["nodes"]["32"]["facilities"]["229"])
check("KeyError 以外でも記録が残る", "did not open" in log_text(),
      log_text()[-400:])
check("例外を握り潰していない", ctx.errors == [], ctx.errors)

print("\n-- 素データの辞書が1つしかない --")
ctx = FakeCtx(OUT_DIR)
if os.path.exists(path):
    os.remove(path)
load_mod().apply(ctx)
world = world_side()
lonely = ShoppingStartManagerRemake(App(world))
hook = ctx.hooks[TARGET]
try:
    hook(ShoppingStartManagerRemake.shopping_start_method_1, lonely)
    raised = None
except KeyError as exc:
    raised = str(exc)
check("素の KeyError がそのまま出る", raised == "'229'", raised)
check("やり直していない", lonely.calls == 1, lonely.calls)
check("何も写していない", shop_of(world, "8", "32", "229") is None)
check("辞書が1つだと分かる記録が出る", "table app.world_dict" in log_text(),
      log_text()[-400:])
check("例外を握り潰していない", ctx.errors == [], ctx.errors)

print("\n-- app が引けない --")
ctx = FakeCtx(OUT_DIR)
if os.path.exists(path):
    os.remove(path)
load_mod().apply(ctx)


class Appless(ShoppingStartManagerRemake):
    def __init__(self):
        self.app = None
        self.target, self.route, self.calls = "229", ("8", "32"), 0

    def shopping_start_method_1(self):
        self.calls += 1
        raise KeyError("229")


appless = Appless()
try:
    ctx.hooks[TARGET](Appless.shopping_start_method_1, appless)
    raised = None
except KeyError as exc:
    raised = str(exc)
check("素の KeyError がそのまま出る", raised == "'229'", raised)
check("app が無いと記録される", "no app" in log_text(), log_text()[-300:])
check("例外を握り潰していない", ctx.errors == [], ctx.errors)

print()
if failures:
    print("FAILED: {}".format(", ".join(failures)))
    raise SystemExit(1)
print("all checks passed")
