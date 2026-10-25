# -*- coding: utf-8 -*-
"""340_inn_shop をゲーム抜きで通す。

    python tools/tests/test_inn_shop.py

確認するもの:

  入口     … 店の `choices["売買する"]` を写し、ゲームの宿屋の `choices` の先頭に足す（`出る` は残る）。
             施設ごとに別の dict にする。店より先に生まれた宿屋は、店が生まれた時点で足す
  除外     … MOD が建てた宿（`mod:` の施設・主が `mod:` の NPC）と、宿屋以外には足さない
  見回り   … 注入より前に生まれていた施設は、選択肢の組み直しの前に世界ごとに1回見回って足す
  素データ … `world_dict` に施設か主が無い宿屋（セーブエディタで足した宿）では入口とボタンを外す。
             揃ったら足し直し、宿屋の画面のボタンに欠けていれば先頭に入れる（1度だけ）
  会話     … 宿屋の主との会話のボタンの先頭に入れる（1度だけ）。主でない人・店の主・素データに無い宿屋の主には入れない
  指示文   … 宿屋の売買の間だけ、ゲームの指示文の2文を差し替える。店では差し替えない。
             終わったら目印を下ろす
  棚       … 宿屋の売買の間は、武器・防具・素材を作らない。食べ物・道具は作る。店では全部作る
"""
import importlib.util
import io
import json
import os
import shutil
import sys
import types
import weakref

HERE = os.path.dirname(os.path.abspath(__file__))
RUNTIME_DIR = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir, "runtime"))
MODS_DIR = os.path.join(RUNTIME_DIR, "mods")
OUT_DIR = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir, "out", "test", "inn_shop"))

if RUNTIME_DIR not in sys.path:
    sys.path.insert(0, RUNTIME_DIR)

import instantale_modloader as ml                      # noqa: E402
from instantale_modloader import llm                   # noqa: E402

failures = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name
          + ((" -- " + str(detail)) if detail and not cond else ""))
    if not cond:
        failures.append(name)


FACILITY_INIT = "__main__:Facility.__init__"
SHOP_TARGET = "__main__:ShoppingStartManagerRemake.execute"
ITEM_TARGET = "__main__:ShoppingStartManagerRemake.generate_item_in_shopping"
REFRESH_TARGET = "__main__:InstantaleApp.refresh_choice_buttons"

class PhaseSpec:
    """ゲームの `PhaseSpec`（`cls_name` と `args`）。"""

    def __init__(self, cls_name, args=()):
        self.cls_name = cls_name
        self.args = list(args)

    def __eq__(self, other):
        return isinstance(other, PhaseSpec) and (self.cls_name, self.args) == (other.cls_name, other.args)


SHOP_SPEC = PhaseSpec("ShoppingStartManagerRemake")
MOVE_SPEC = PhaseSpec("MovePhaseManager")

GAME_SENTENCE_1 = ("必ず最低バリュー段階の武器と防具をそれぞれ一つずつ生成し、"
                   "また許可されたリストの各段階ごとの回復アイテムを一つずつ生成する。"
                   "それに加えて自由にいくつか作成する。")
GAME_SENTENCE_2 = "必ず武器と防具をそれぞれ一つずつ生成する。"


class Facility:
    """ゲームの施設。`__init__` の中身は検査が `orig` として演じる。"""


def make_facility(ctx, facility_id, facility_type, owner, hooked=True):
    facility = Facility.__new__(Facility)

    def orig(self, *args, **kwargs):
        self.id = facility_id
        self.name = "テスト" + facility_type
        self.facility_type = facility_type
        self.owner = owner
        self.config = {"level_of_detail": 0}
        if facility_type in ("general_store", "specialty_shop"):
            self.choices = {"売買する": SHOP_SPEC, "出る": MOVE_SPEC}
        else:
            self.choices = {"出る": MOVE_SPEC}
        return None

    if hooked:
        ctx.hooks[FACILITY_INIT](orig, facility, None, None, {})
    else:
        orig(facility)
    return facility


class Node:
    def __init__(self, facilities):
        self.id = "0"
        self.facilities = {f.id: f for f in facilities}


class Area:
    def __init__(self, area_id, facilities):
        self.id = area_id
        self.name = "テストの街"
        self.nodes = {"0": Node(facilities)}


class FakeCtx:
    _mod = "340_inn_shop"

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


REWRITES = []


def fake_wrap_outgoing(ctx, rewrite, **kw):
    REWRITES.append(rewrite)
    return None


def setup():
    shutil.rmtree(OUT_DIR, ignore_errors=True)
    os.makedirs(OUT_DIR, exist_ok=True)
    folder = os.path.join(MODS_DIR, "340_inn_shop")
    with io.open(os.path.join(folder, "mod.json"), encoding="utf-8") as fh:
        entry = json.load(fh)["entry"]
    spec = importlib.util.spec_from_file_location("inn_shop_mod", os.path.join(folder, entry))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if hasattr(sys, module.STORE_ATTR):
        delattr(sys, module.STORE_ATTR)
    saved, llm.wrap_outgoing = llm.wrap_outgoing, fake_wrap_outgoing
    REWRITES.clear()
    try:
        ctx = FakeCtx(OUT_DIR)
        module.apply(ctx)
    finally:
        llm.wrap_outgoing = saved
    return module, ctx


def log_text():
    path = os.path.join(OUT_DIR, "inn_shop.log")
    if not os.path.exists(path):
        return ""
    with io.open(path, encoding="utf-8") as fh:
        return fh.read()


print("入口")
module, ctx = setup()
shop = make_facility(ctx, "3", "general_store", "1")
inn = make_facility(ctx, "7", "inn", "4")
check("宿屋の先頭に 売買する", list(inn.choices) == ["売買する", "出る"], inn.choices)
check("値は店の写し", inn.choices["売買する"] == SHOP_SPEC, inn.choices)
check("出るはそのまま", inn.choices["出る"] is MOVE_SPEC, inn.choices)
check("店は触らない", list(shop.choices) == ["売買する", "出る"], shop.choices)
other = make_facility(ctx, "8", "inn", "5")
check("施設ごとに別の dict", other.choices is not inn.choices)
check("入口を写した記録", "entry: copied" in log_text(), log_text())

print("店より先に生まれた宿屋")
module, ctx = setup()
early = make_facility(ctx, "7", "inn", "4")
check("店が無い間は足さない", list(early.choices) == ["出る"], early.choices)
make_facility(ctx, "3", "general_store", "1")
check("店が生まれた時点で足す", list(early.choices) == ["売買する", "出る"], early.choices)

print("除外")
mod_inn = make_facility(ctx, "mod:331_facility_investment:inn", "inn", "4")
check("MOD の施設には足さない", list(mod_inn.choices) == ["出る"], mod_inn.choices)
mod_owner = make_facility(ctx, "9", "inn", "mod:331_facility_investment:keeper")
check("主が MOD の NPC なら足さない", list(mod_owner.choices) == ["出る"], mod_owner.choices)
no_owner = make_facility(ctx, "10", "inn", None)
check("主が居なければ足さない", list(no_owner.choices) == ["出る"], no_owner.choices)
guild = make_facility(ctx, "11", "guild", "6")
check("宿屋以外には足さない", list(guild.choices) == ["出る"], guild.choices)

def module_store():
    return getattr(sys, "_instantale_inn_shop_store")


class FakeWorld(object):
    """ゲームの World と同じく弱い参照を取れる世界（SimpleNamespace は取れない）。"""

    def __init__(self, **kw):
        self.__dict__.update(kw)


print("見回り（注入より前に生まれた施設）")
module, ctx = setup()
old_shop = make_facility(ctx, "3", "general_store", "1", hooked=False)
old_inn = make_facility(ctx, "7", "inn", "4", hooked=False)
area = Area("0", [old_shop, old_inn])
world = FakeWorld(name="テスト世界", areas={"0": area}, characters={})
player = types.SimpleNamespace(name="テストプレイヤー", current_area="0", location=old_inn)
app = types.SimpleNamespace(world=world, player=player, buttons=[])
refresh = ctx.hooks[REFRESH_TARGET]
refresh(lambda self, *a, **k: "drawn", app)
check("見回りで宿屋に足す", list(old_inn.choices) == ["売買する", "出る"], old_inn.choices)
check("見回りの記録", "sweep: 1 inn(s)" in log_text(), log_text())
refresh(lambda self, *a, **k: "drawn", app)
check("同じ世界は2度見回らない", log_text().count("sweep:") == 1, log_text())
check("見回った世界は弱く持つ", isinstance(module_store()["swept"], weakref.ref), module_store()["swept"])
app.world = FakeWorld(name="テスト世界", areas={"0": area}, characters={})
refresh(lambda self, *a, **k: "drawn", app)
check("別の世界に替わったら見回る", log_text().count("sweep:") == 2, log_text())
check("例外を出していない", not ctx.errors, ctx.errors)

print("素データに無い宿屋（セーブエディタで足した宿）")
module, ctx = setup()
shop2 = make_facility(ctx, "3", "general_store", "1")
listed = make_facility(ctx, "7", "inn", "4")
edited = make_facility(ctx, "228", "inn", "98")
area2 = Area("8", [shop2, listed, edited])
world2 = types.SimpleNamespace(name="テスト世界2", areas={"8": area2}, characters={})
world_dict = {"areas": {"8": {"nodes": {"0": {"facilities": {"3": {}, "7": {}}}}}},
              "npcs": {"1": {}, "4": {}}}
player2 = types.SimpleNamespace(name="テストプレイヤー", current_area="8", location=edited)
app2 = types.SimpleNamespace(world=world2, player=player2, world_dict=world_dict,
                             buttons=[{"text": "売買する", "spec": None}])
spec_cls = __import__("instantale_modloader.ui", fromlist=["ui"]).spec_cls_name
buttons2 = [{"text": t, "cls": c} for t, c in (("売買する", "ShoppingStartManagerRemake"),
                                              ("宿泊する(3ヵ月)", "DisplayVacationChoice"),
                                              ("出る", "MovePhaseManager"))]


class Spec:
    def __init__(self, cls_name):
        self.cls_name = cls_name


for entry in buttons2:
    entry["spec"] = Spec(entry.pop("cls"))
app2.buttons = buttons2
refresh2 = ctx.hooks[REFRESH_TARGET]
refresh2(lambda self, *a, **k: "drawn", app2)
check("素データに無い宿屋は入口を外す", list(edited.choices) == ["出る"], edited.choices)
check("組み直す前のボタンからも外す", [e["text"] for e in app2.buttons] == ["宿泊する(3ヵ月)", "出る"],
      [e["text"] for e in app2.buttons])
check("外した記録", "withheld at inn 228" in log_text(), log_text())
# 素データが後から揃った（エディタで写した）。組み直しで足し直し、組み終わったボタンにも入れる。
world_dict["areas"]["8"]["nodes"]["0"]["facilities"]["228"] = {}
world_dict["npcs"]["98"] = {}
refresh2(lambda self, *a, **k: "drawn", app2)
check("揃ったら入口を足し直す", list(edited.choices) == ["売買する", "出る"], edited.choices)
check("その画面のボタンにも入れる", [e["text"] for e in app2.buttons] == ["売買する", "宿泊する(3ヵ月)", "出る"],
      [e["text"] for e in app2.buttons])
refresh2(lambda self, *a, **k: "drawn", app2)
check("2度は入れない", [e["text"] for e in app2.buttons].count("売買する") == 1, app2.buttons)
app2.buttons = [{"text": "相部屋(10円)", "spec": Spec("VacationStartManager")}]
edited.choices = {"出る": MOVE_SPEC}
refresh2(lambda self, *a, **k: "drawn", app2)
check("宿屋の画面でなければボタンには入れない", [e["text"] for e in app2.buttons] == ["相部屋(10円)"],
      app2.buttons)
player2.location = listed
refresh2(lambda self, *a, **k: "drawn", app2)
check("素データに在る宿屋は入口を残す", list(listed.choices) == ["売買する", "出る"], listed.choices)
world_dict["npcs"].pop("4")
player2.location = listed
app2.buttons = []
refresh2(lambda self, *a, **k: "drawn", app2)
check("主が素データに無ければ外す", list(listed.choices) == ["出る"], listed.choices)
from instantale_modloader import save_lookup  # noqa: E402
app2.save_data_dict = {"areas": world_dict["areas"], "npcs": {"1": {}, "4": {}, "98": {}}}
with save_lookup.redirected(app2, "test"):
    app2.buttons = []
    refresh2(lambda self, *a, **k: "drawn", app2)
check("差し替えの間も本来の世界で判断する", list(listed.choices) == ["出る"], listed.choices)
save_lookup.reset()
check("例外を出していない（素データ）", not ctx.errors, ctx.errors)

# 売買を直す MOD（146）が窓口に名乗っていれば、素データに無い宿屋でも入口を出す。
save_lookup.reset()
save_lookup.cover("146_fix_save_only_entities", ctx, "shopping")
listed.choices = {"出る": MOVE_SPEC}
player2.location = listed
app2.buttons = []
refresh2(lambda self, *a, **k: "drawn", app2)
check("直す MOD が居れば素データに無くても入口を足す", list(listed.choices) == ["売買する", "出る"], listed.choices)
save_lookup.reset()

print("宿屋の主との会話")
module, ctx = setup()
shop3 = make_facility(ctx, "3", "general_store", "1")
inn3 = make_facility(ctx, "7", "inn", "4")
area3 = Area("9", [shop3, inn3])
world3 = types.SimpleNamespace(name="テスト世界3", areas={"9": area3}, characters={})
world_dict3 = {"areas": {"9": {"nodes": {"0": {"facilities": {"3": {}, "7": {}}}}}},
               "npcs": {"1": {}, "4": {}, "5": {}}}
player3 = types.SimpleNamespace(name="テストプレイヤー", current_area="9", location=inn3)


def talk_buttons(partner):
    end = Spec("ConversationEndManager")
    end.args = [partner, None, ""]
    return [{"text": "この話から依頼を作る（テスト）", "spec": Spec("QuestFromConversation")},
            {"text": "会話を終了する", "spec": end}]


app3 = types.SimpleNamespace(world=world3, player=player3, world_dict=world_dict3,
                             buttons=talk_buttons("4"))
refresh3 = ctx.hooks[REFRESH_TARGET]
refresh3(lambda self, *a, **k: "drawn", app3)
check("主との会話の先頭に 売買する", [e["text"] for e in app3.buttons]
      == ["売買する", "この話から依頼を作る（テスト）", "会話を終了する"], [e["text"] for e in app3.buttons])
check("値は店の写し（会話）", app3.buttons[0]["spec"] == SHOP_SPEC, app3.buttons[0])
check("会話に入れた記録", "in the conversation with owner 4 at inn 7" in log_text(), log_text())
refresh3(lambda self, *a, **k: "drawn", app3)
check("会話でも2度は入れない", [e["text"] for e in app3.buttons].count("売買する") == 1, app3.buttons)
app3.buttons = talk_buttons("5")
refresh3(lambda self, *a, **k: "drawn", app3)
check("主でない人との会話には入れない", "売買する" not in [e["text"] for e in app3.buttons],
      [e["text"] for e in app3.buttons])
player3.location = shop3
app3.buttons = talk_buttons("1")
refresh3(lambda self, *a, **k: "drawn", app3)
check("店の主との会話は触らない（ゲームが足す）", "売買する" not in [e["text"] for e in app3.buttons],
      [e["text"] for e in app3.buttons])
world_dict3["npcs"].pop("4")
player3.location = inn3
app3.buttons = talk_buttons("4")
refresh3(lambda self, *a, **k: "drawn", app3)
check("素データに無い宿屋の主との会話には入れない", "売買する" not in [e["text"] for e in app3.buttons],
      [e["text"] for e in app3.buttons])
check("例外を出していない（会話）", not ctx.errors, ctx.errors)

print("指示文と棚")
check("送り口に1つ登録", len(REWRITES) == 1, REWRITES)
rewrite = REWRITES[0]
prompt = "【指示】\n" + GAME_SENTENCE_1 + "\n【エリアの構造】"
prompt_2 = "【指示】\n" + GAME_SENTENCE_2 + "\n【エリアの構造】"
check("売買の外では差し替えない", rewrite([prompt], "chat") is None)

made = []


class Manager:
    def __init__(self, app):
        self.app = app

    def generate_item_in_shopping(self, item_data, shop_owner_instance, item_stock_tier):
        made.append(item_data["item_name"])


def item(name, kind):
    return {"item_name": name, "item_category": {"type": kind, "sub_type": "x"}, "value": 3}


seen = {}


def at_inn_orig(self, choice_text):
    seen["texts"] = rewrite([prompt, prompt_2, "関係の無い頼み"], "chat")
    hook = ctx.hooks[ITEM_TARGET]
    for name, kind in (("剣", "weapon"), ("胸当て", "wearable"), ("鉄くず", "material"),
                       ("煮込み", "healing_item"), ("麦酒", "consumable"), ("燭台", "utility")):
        hook(Manager.generate_item_in_shopping, self, item(name, kind), None, 1)
    return "shopping"


manager = Manager(app)
result = ctx.hooks[SHOP_TARGET](at_inn_orig, manager, "売買する")
texts = seen.get("texts") or ["", "", ""]
check("orig の戻り値をそのまま返す", result == "shopping", result)
check("1文目を差し替える", GAME_SENTENCE_1 not in texts[0] and "食料・飲み物・酒" in texts[0], texts[0])
check("2文目を差し替える", GAME_SENTENCE_2 not in texts[1] and "料理" in texts[1], texts[1])
check("ほかの文はそのまま", texts[2] == "関係の無い頼み", texts[2])
check("武器・防具・素材は作らない", made == ["煮込み", "麦酒", "燭台"], made)
check("棚の記録", "dropped '剣'" in log_text() and "done at inn 7" in log_text(), log_text())
check("終わったら目印を下ろす", rewrite([prompt], "chat") is None)

print("店では素のまま")
made.clear()
player.location = old_shop
seen.clear()
ctx.hooks[SHOP_TARGET](at_inn_orig, manager, "売買する")
check("店では差し替えない", seen.get("texts") is None, seen)
check("店では全部作る", len(made) == 6, made)

print("引数はそのまま渡す")
passed = {}


def tier_default(self, item_data, shop_owner_instance=None, item_stock_tier=2):
    passed["tier"] = item_stock_tier
    passed["item"] = item_data["item_name"]


ctx.hooks[ITEM_TARGET](tier_default, manager, item_data=item("剣", "weapon"))
check("店で省いた段は既定のまま", passed.get("tier") == 2, passed)
player.location = inn
passed.clear()


def at_inn_keyword(self, choice_text):
    hook = ctx.hooks[ITEM_TARGET]
    hook(tier_default, self, item_data=item("煮込み", "healing_item"))
    hook(tier_default, self, item_data=item("剣", "weapon"))
    return "shopping"


ctx.hooks[SHOP_TARGET](at_inn_keyword, manager, "売買する")
check("宿で名前付きの品も見分ける", passed.get("item") == "煮込み", passed)
check("宿で省いた段は既定のまま", passed.get("tier") == 2, passed)
player.location = old_shop
check("例外を出していない（最後）", not ctx.errors, ctx.errors)

if failures:
    print("FAILED: " + ", ".join(failures))
    sys.exit(1)
print("OK")
