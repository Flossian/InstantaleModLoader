# -*- coding: utf-8 -*-
"""340_shop_lineup をゲーム抜きで通す。

    python tools/tests/test_shop_lineup.py

確認するもの:

  入口     … 店の `choices["売買する"]` を写し、ゲームの宿屋と鍛冶屋の `choices` の先頭に足す（`出る` は残る）。
             施設ごとに別の dict にする。店より先に生まれた宿屋は、店が生まれた時点で足す
  除外     … MOD が建てた施設（`mod:` の施設・主が `mod:` の NPC）と、宿屋・鍛冶屋以外には足さない
  見回り   … 注入より前に生まれていた施設は、選択肢の組み直しの前に世界ごとに1回見回って足す
  素データ … `world_dict` に施設か主が無い宿屋（セーブエディタで足した宿）では入口とボタンを外す。
             揃ったら足し直し、施設の画面のボタンに欠けていれば先頭に入れる（1度だけ）。鍛冶屋の画面にも入れる
  会話     … 宿屋・鍛冶屋の主との会話のボタンの先頭に入れる（1度だけ）。主でない人・店の主・素データに無い宿屋の主には入れない
  傾向     … 店の売買の間だけ、ゲームのプロンプトの2文を店の種類ごとの文へ差し替える。
             雑貨屋の2文目と、店でない施設・MOD の施設は差し替えない。終わったら目印を下ろす。
             品は種類で落とさない（混入は可）
  窓口     … 傾向の一文を窓口 `shop_lineup` に出す（312 の入れ替えが聞く）
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
OUT_DIR = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir, "out", "test", "shop_lineup"))

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
    _mod = "340_shop_lineup"

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
    folder = os.path.join(MODS_DIR, "340_shop_lineup")
    with io.open(os.path.join(folder, "mod.json"), encoding="utf-8") as fh:
        entry = json.load(fh)["entry"]
    spec = importlib.util.spec_from_file_location("shop_lineup_mod", os.path.join(folder, entry))
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
    path = os.path.join(OUT_DIR, "shop_lineup.log")
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
check("宿屋・鍛冶屋以外には足さない", list(guild.choices) == ["出る"], guild.choices)
smith = make_facility(ctx, "12", "blacksmith", "7")
check("鍛冶屋にも足す", list(smith.choices) == ["売買する", "出る"], smith.choices)

def module_store():
    return getattr(sys, "_instantale_shop_lineup_store")


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
check("見回りの記録", "sweep: '売買する' added to {'inn': 1}" in log_text(), log_text())
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

print("鍛冶屋の画面と主との会話")
module, ctx = setup()
shop4 = make_facility(ctx, "3", "general_store", "1")
smith4 = make_facility(ctx, "20", "blacksmith", "9")
area4 = Area("5", [shop4, smith4])
world4 = types.SimpleNamespace(name="テスト世界4", areas={"5": area4}, characters={})
world_dict4 = {"areas": {"5": {"nodes": {"0": {"facilities": {"3": {}, "20": {}}}}}},
               "npcs": {"1": {}, "9": {}}}
player4 = types.SimpleNamespace(name="テストプレイヤー", current_area="5", location=smith4)
app4 = types.SimpleNamespace(world=world4, player=player4, world_dict=world_dict4,
                             buttons=[{"text": "装備の強化", "spec": Spec("EquipmentReinforcementStart")},
                                      {"text": "出る", "spec": Spec("MovePhaseManager")},
                                      {"text": "会話する", "spec": Spec("DisplayTalkChoice")}])
refresh4 = ctx.hooks[REFRESH_TARGET]
refresh4(lambda self, *a, **k: "drawn", app4)
check("鍛冶屋の画面の先頭に 売買する", [e["text"] for e in app4.buttons] == ["売買する", "装備の強化", "出る", "会話する"],
      [e["text"] for e in app4.buttons])
check("鍛冶屋の画面に入れた記録", "on the current screen at blacksmith 20" in log_text(), log_text())
app4.buttons = talk_buttons("9")
refresh4(lambda self, *a, **k: "drawn", app4)
check("鍛冶屋の主との会話の先頭に 売買する", app4.buttons[0]["text"] == "売買する",
      [e["text"] for e in app4.buttons])
check("例外を出していない（鍛冶屋）", not ctx.errors, ctx.errors)

print("品揃えの傾向")
check("送り口に1つ登録", len(REWRITES) == 1, REWRITES)
rewrite = REWRITES[0]
prompt = "【指示】\n" + GAME_SENTENCE_1 + "\n【現在地】\nテスト{kind}"
prompt_2 = "【指示】\n" + GAME_SENTENCE_2 + "\n【エリアの構造】"
check("売買の外では差し替えない", rewrite([prompt], "chat") is None)


class Manager:
    def __init__(self, app):
        self.app = app


seen = {}


def make_orig(kind):
    def orig(self, choice_text):
        seen["texts"] = rewrite([prompt.format(kind=kind), prompt_2, "関係の無い頼み"], "chat")
        return "shopping"
    return orig


def shop_at(kind, facility_id="30", owner="8"):
    facility = make_facility(ctx, facility_id, kind, owner, hooked=False)
    facility.config = {"goods": [{"item_category": {"type": "weapon"}}, {"item_category": {"type": "healing_item"}}]}
    app5 = types.SimpleNamespace(world=world4, player=types.SimpleNamespace(location=facility, current_area="5"))
    seen.clear()
    result = ctx.hooks[SHOP_TARGET](make_orig(kind), Manager(app5), "売買する")
    return result, seen.get("texts")


result, texts = shop_at("inn")
check("orig の戻り値をそのまま返す", result == "shopping", result)
check("宿屋: 1文目を料理・飲み物・酒の文へ", GAME_SENTENCE_1 not in texts[0] and "料理・飲み物・酒" in texts[0]
      and "回復薬" in texts[0], texts[0])
check("宿屋: 2文目も差し替える", GAME_SENTENCE_2 not in texts[1] and "料理" in texts[1], texts[1])
check("ほかの文はそのまま", texts[2] == "関係の無い頼み", texts[2])
check("終わりの記録（種類の内訳）", "done at inn 30: rewrites=2 dropped=0 goods=2" in log_text(), log_text())
check("店の名前がプロンプトに載っていたかの記録", "shop name in prompt: yes" in log_text(), log_text())
check("終わったら目印を下ろす", rewrite([prompt], "chat") is None)
_r, texts = shop_at("blacksmith")
check("鍛冶屋: 武器と防具が中心", "武器と防具が中心" in texts[0] and GAME_SENTENCE_1 not in texts[0], texts[0])
check("鍛冶屋: 混入は可と頼む", "混じってもよい" in texts[0], texts[0])
_r, texts = shop_at("medical_facility")
check("診療所: 回復薬・ポーション・薬", "回復薬・ポーション・薬" in texts[0], texts[0])
_r, texts = shop_at("specialty_shop")
check("専門店: 看板に合う品", "店の名前と説明に合う" in texts[0] and "看板に合う品を二つ" in texts[1], texts)
_r, texts = shop_at("general_store")
check("雑貨屋: 幅広く", "幅広く" in texts[0] and GAME_SENTENCE_1 not in texts[0], texts[0])
check("雑貨屋: 2文目はゲームのまま", texts[1] == prompt_2, texts[1])
_r, texts = shop_at("guild")
check("店でない施設は差し替えない", texts is None, texts)
_r, texts = shop_at("blacksmith", facility_id="mod:331_facility_investment:smith")
check("MOD の施設は差し替えない", texts is None, texts)

print("世界の一括生成の店の仕事")
BULK_TARGET = "scripts.world_bulk_backend:GameGenerationBackend.generate"
bulk_world = {"areas": {"0": {"nodes": {"0": {"facilities": {
    "5": {"name": "テスト診療所", "facility_type": "medical_facility", "owner": "8", "config": {}},
    "6": {"name": "テスト役場", "facility_type": "administrative_office", "owner": "9", "config": {}}}}}}}}


def bulk_orig(kind):
    def orig(self, task, world):
        seen["texts"] = rewrite([prompt.format(kind=kind), prompt_2], "chat")
        world["areas"]["0"]["nodes"]["0"]["facilities"][task.ids[2]]["config"]["goods"] = [
            {"item_category": {"type": "healing_item"}}]
        return "made"
    return orig


seen.clear()
task = types.SimpleNamespace(kind="shop", ids=("0", "0", "5", 1), label="テスト診療所 (1/3)")
result = ctx.hooks[BULK_TARGET](bulk_orig("診療所"), object(), task, bulk_world)
check("一括生成: orig の戻り値をそのまま返す", result == "made", result)
check("一括生成: 診療所の傾向へ差し替える", "回復薬・ポーション・薬" in (seen.get("texts") or [""])[0], seen)
check("一括生成: 終わりの記録", "bulk done at medical_facility 5 'テスト診療所 (1/3)': rewrites=2 dropped=0 goods=1" in log_text(),
      log_text())
check("一括生成: 終わったら目印を下ろす", rewrite([prompt], "chat") is None)
seen.clear()
ctx.hooks[BULK_TARGET](bulk_orig("役場"), object(), types.SimpleNamespace(kind="shop", ids=("0", "0", "6", 1)), bulk_world)
check("一括生成: 店でない施設は差し替えない", seen.get("texts") is None, seen)
seen.clear()
ctx.hooks[BULK_TARGET](lambda self, task, world: "npc", object(), types.SimpleNamespace(kind="npc", ids=("3",)), bulk_world)
check("一括生成: 店の仕事でなければ触らない", seen.get("texts") is None and not ctx.errors, (seen, ctx.errors))

print("合わない武器・防具（設定 MIXED_GEAR）")
ITEM_TARGET = "__main__:ShoppingStartManagerRemake.generate_item_in_shopping"
made = []


def gear_orig(kind):
    def orig(self, choice_text):
        seen["texts"] = rewrite([prompt.format(kind=kind), prompt_2], "chat")
        hook = ctx.hooks[ITEM_TARGET]
        for name, cat in (("剣", "weapon"), ("胸当て", "wearable"), ("煮込み", "healing_item"), ("鉄くず", "material")):
            hook(lambda s, item_data, *a, **k: made.append(item_data["item_name"]), self,
                 {"item_name": name, "item_category": {"type": cat}}, None, 1)
        return "shopping"
    return orig


def gear_shop(kind):
    facility = make_facility(ctx, "40", kind, "8", hooked=False)
    facility.config = {}
    app6 = types.SimpleNamespace(world=world4, player=types.SimpleNamespace(location=facility, current_area="5"))
    seen.clear()
    del made[:]
    ctx.hooks[SHOP_TARGET](gear_orig(kind), Manager(app6), "売買する")
    return facility


check("既定は混ぜる", module.MIXED_GEAR is True)
gear_shop("inn")
check("既定: 宿屋でも武器・防具を作る", made == ["剣", "胸当て", "煮込み", "鉄くず"], made)
check("既定: 作らないとは頼まない", "武器と防具は作らない" not in seen["texts"][0], seen["texts"][0])
module.MIXED_GEAR = False
inn6 = gear_shop("inn")
check("切: 宿屋は武器と防具を作らないと頼む", "武器と防具は作らない" in seen["texts"][0]
      and "武器と防具は作らない" in seen["texts"][1], seen["texts"])
check("切: 宿屋で返ってきた武器・防具は作らない（素材は残す）", made == ["煮込み", "鉄くず"], made)
check("切: 外した記録", "dropped '剣' (weapon) at inn 40" in log_text() and "dropped=2" in log_text(), log_text())
TEMPLATE_TARGET = "__main__:ShoppingStartManagerRemake.set_item_from_world_data"
keeper = types.SimpleNamespace(inventory=types.SimpleNamespace(inventory={"old": types.SimpleNamespace(name="前の剣", item_type="weapon")}))


def template_orig(kind):
    def orig(self, choice_text):
        def lay(s, shop_owner_instance, next_tier):
            stock = shop_owner_instance.inventory.inventory
            for key, name, cat in (("n1", "短剣", "weapon"), ("n2", "胸当て", "wearable"), ("n3", "薬草", "healing_item")):
                stock[key] = types.SimpleNamespace(name=name, item_type=cat)
        ctx.hooks[TEMPLATE_TARGET](lay, self, keeper, 1)
        return "shopping"
    return orig


def template_shop(kind):
    facility = make_facility(ctx, "41", kind, "8", hooked=False)
    facility.config = {}
    app7 = types.SimpleNamespace(world=world4, player=types.SimpleNamespace(location=facility, current_area="5"))
    keeper.inventory.inventory = {"old": types.SimpleNamespace(name="前の剣", item_type="weapon")}
    ctx.hooks[SHOP_TARGET](template_orig(kind), Manager(app7), "売買する")
    return sorted(keeper.inventory.inventory)


check("切: 雛形から並べた武器・防具も外す（前からの品は残す）", template_shop("medical_facility") == ["n3", "old"],
      template_shop("medical_facility"))
check("切: 雛形から外した記録", "dropped '短剣' (weapon) from the stock template at medical_facility 41" in log_text(),
      log_text())
check("切: 鍛冶屋の雛形は外さない", template_shop("blacksmith") == ["n1", "n2", "n3", "old"], keeper.inventory.inventory)

# 棚が空から並べた回（初めて開いた店）で武器・防具を外したら、傾向の品を 2〜6 品足す。
asked_fill = []


def fake_ask(ctx_, manager, messages, **kw):
    asked_fill.append(messages[0]["content"])
    return {"items": [
        {"item_name": "足した薬", "description": "", "item_type": "healing_item", "item_sub_type": "medicine",
         "value": 99, "item_appearance": "a jar", "rarity": "common"},
        {"item_name": "足した剣", "description": "", "item_type": "weapon", "item_sub_type": "small",
         "value": 30, "item_appearance": "a sword", "rarity": "common"},
        {"item_name": "足した札", "description": "", "item_type": "consumable", "item_sub_type": "nope",
         "value": "x", "item_appearance": "a charm", "rarity": "odd"},
        {"item_name": "足した茶", "description": "", "item_type": "healing_item", "item_sub_type": "drink",
         "value": 20, "item_appearance": "tea", "rarity": "rare"},
        {"item_name": "足した包帯", "description": "", "item_type": "utility", "item_sub_type": "tool",
         "value": 20, "item_appearance": "bandage", "rarity": "common"},
        {"item_name": "足した軟膏", "description": "", "item_type": "healing_item", "item_sub_type": "medicine",
         "value": 20, "item_appearance": "salve", "rarity": "common"}]}


built = []


def fill_shop(kind, start_empty=True):
    facility = make_facility(ctx, "42", kind, "8", hooked=False)
    facility.config = {"goods": [{"value": 31, "stock_tier": 1, "item_type": "weapon"}, {"value": 33, "stock_tier": 1, "item_type": "healing_item", "name": "雛形の湿布"}, {"value": 50, "stock_tier": 2}]}
    stock = {} if start_empty else {"old": types.SimpleNamespace(name="前の薬", item_type="healing_item")}

    def maker(*fields):
        built.append(fields)
        fields[-1].inventory.inventory["m{}".format(len(built))] = types.SimpleNamespace(name=fields[0], item_type=fields[2])
    app8 = types.SimpleNamespace(world=world4, player=types.SimpleNamespace(location=facility, current_area="5"),
                                 generate_item_from_item_data=maker)
    keeper.inventory.inventory = stock

    def orig(self, choice_text):
        def lay(s, shop_owner_instance, next_tier):
            for key, name, cat in (("n1", "短剣", "weapon"), ("n3", "薬草", "healing_item")):
                shop_owner_instance.inventory.inventory[key] = types.SimpleNamespace(name=name, item_type=cat)
        ctx.hooks[TEMPLATE_TARGET](lay, self, keeper, 1)
        return "shopping"
    del asked_fill[:]
    del built[:]
    ctx.hooks[SHOP_TARGET](orig, Manager(app8), "売買する")


saved_ask, saved_structure, saved_randint = llm.ask, llm.create_structure, module.random.randint
llm.ask = fake_ask
llm.create_structure = lambda *a, **k: object()
module.random.randint = lambda lo, hi: hi
try:
    fill_shop("medical_facility")
    names = sorted(item.name for item in keeper.inventory.inventory.values())
    check("足す: 初めての棚で武器を外したら傾向の品を足す", "足した薬" in names and "足した茶" in names and "短剣" not in names, names)
    check("足す: 頼みは 2〜6 の上限の数・段の値・傾向・棚の品", asked_fill and "あと6個" in asked_fill[0]
          and "[31, 33, 31, 33, 31, 33]" in asked_fill[0] and "回復薬・ポーション" in asked_fill[0]
          and "武器と防具は作らない" in asked_fill[0] and "雛形の湿布" in asked_fill[0], asked_fill)
    check("足す: 頼みの語彙に武器・防具が無い", asked_fill and "weapon:" not in asked_fill[0] and "wearable:" not in asked_fill[0])
    check("足す: 返ってきた武器は作らない", all(f[2] != "weapon" for f in built), built)
    check("足す: 外れた値は寄せる（価値 1〜70・段の値・細分・希少度）",
          built[0][4] == 70 and built[1][3] == "food" and built[1][4] == 31 and built[1][6] == "common", built)
    check("足す: 記録", "filled 5 of 6 item(s) at medical_facility 42" in log_text(), log_text())
    fill_shop("medical_facility", start_empty=False)
    check("足す: 前から品のある棚には足さない", asked_fill == [] and built == [], (asked_fill, built))
    module.MIXED_GEAR = True
    fill_shop("medical_facility")
    check("足す: 混ぜる設定なら足さない", asked_fill == [] and built == [], (asked_fill, built))
    module.MIXED_GEAR = False
finally:
    llm.ask, llm.create_structure, module.random.randint = saved_ask, saved_structure, saved_randint
gear_shop("medical_facility")
check("切: 診療所も外す", made == ["煮込み", "鉄くず"], made)
gear_shop("blacksmith")
check("切: 鍛冶屋は外さない", made == ["剣", "胸当て", "煮込み", "鉄くず"], made)
gear_shop("specialty_shop")
check("切: 専門店は外さない（看板が武器のこともある）", made == ["剣", "胸当て", "煮込み", "鉄くず"], made)
from instantale_modloader import shop_lineup  # noqa: E402
check("切: 窓口が宿屋の武器・防具を並べない種類として出す", shop_lineup.excluded_of(inn6) == ("weapon", "wearable"),
      shop_lineup.excluded_of(inn6))
check("切: 窓口の傾向にも一文が入る", "武器と防具は作らない" in (shop_lineup.lineup_of(inn6)[0] or ""))
module.MIXED_GEAR = True
check("入: 雛形の武器・防具も外さない", template_shop("medical_facility") == ["n1", "n2", "n3", "old"], keeper.inventory.inventory)
check("入: 窓口は並べない種類を出さない", shop_lineup.excluded_of(inn6) == (), shop_lineup.excluded_of(inn6))
check("例外を出していない（武器・防具）", not ctx.errors, ctx.errors)

print("窓口 shop_lineup")
from instantale_modloader import shop_lineup  # noqa: E402
inn5 = make_facility(ctx, "31", "inn", "8", hooked=False)
text, by = shop_lineup.lineup_of(inn5)
check("宿屋の傾向を出す", text == module.LINEUPS["inn"]["tendency"] and by == "340_shop_lineup", (text, by))
check("店でない施設には口を出さない", shop_lineup.lineup_of(make_facility(ctx, "32", "guild", "8", hooked=False)) == (None, None))
check("例外を出していない（最後）", not ctx.errors, ctx.errors)
shop_lineup.reset()

if failures:
    print("FAILED: " + ", ".join(failures))
    sys.exit(1)
print("OK")
