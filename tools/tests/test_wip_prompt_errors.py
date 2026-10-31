# -*- coding: utf-8 -*-
"""921_fix_prompt_errors（開発中）をゲーム抜きで通す。

    python tools/tests/test_wip_prompt_errors.py

pydantic の代わりの型（`create_model` / `Field` / `model_fields`）でゲームの `Area` を組み、次を確認する。

  表         … 頼みごとの直し（RULES）がそれぞれの頼みの文に当たり、ほかの頼み・MOD の頼みには当たらない。
               想定段階は難易度から（序盤・中盤・終盤）、読めなければ行ごと外す。元の本文は変えない
  頼み       … 【構造の生成要素】と【生成ルール】を1つに書き直す。自由生成施設の段は元にあるときだけ。
               ほかの節と元の本文は変えない。まとまりが無ければ触らない
  確かめる   … 必須の5施設（区画の中も数える。Enum の値も読む）と、住人0人・冒険者2人未満を見つける。
               型の選べる値に無い種類は数えない
  頼み直しの型 … 欠けた種類だけ（置く区画＋施設の欄、type は除く）と足りない並びだけ
  足す       … 名前の合う区画へ、無ければ最上位へ。人物は並びの後ろへ。新しい施設の欄は元の並び。
               最初の返事の形（型・辞書・JSON）で返す
  包み       … 街の生成の頼みだけ書き直して送る。欠けが無ければ1回だけ。
               欠けがあれば頼み直しの名前・欠けた分の型・最初の返事と欠けの頼みを続けて1回だけ送り、元の型で返す。
               頼み直しが失敗したら最初の返事を返す。ほかの頼み・頼み直しの頼みは街の確かめをしない
               ほかの頼みも表を当てて送る。同じ呼び出しで包みが重なっても1回だけ当てる。記録は組み合わせごとに初回

モデルでの効き目は MOD の DOC.md §4 の試しで見る。
"""
import enum
import importlib.util
import io
import json
import os
import sys
from typing import List, Union

HERE = os.path.dirname(os.path.abspath(__file__))
RUNTIME_DIR = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir, "runtime"))
MODS_DIR = os.path.join(RUNTIME_DIR, "mods")
OUT_DIR = os.path.normpath(os.path.join(HERE, os.pardir, os.pardir, "out", "test"))
if RUNTIME_DIR not in sys.path:
    sys.path.insert(0, RUNTIME_DIR)

import instantale_modloader as ml  # noqa: E402

SEND = "scripts.llm.llm_manager_world_generate:send_request"
LOG_NAME = "prompt_errors.log"


def find_mod(suffix):
    matches = sorted(name for name in os.listdir(MODS_DIR)
                     if name.endswith(suffix)
                     and os.path.isfile(os.path.join(MODS_DIR, name, "mod.json")))
    if len(matches) != 1:
        raise SystemExit("cannot find *{} in {}: {}".format(suffix, MODS_DIR, matches))
    folder = os.path.join(MODS_DIR, matches[0])
    with io.open(os.path.join(folder, "mod.json"), encoding="utf-8") as fh:
        return os.path.join(folder, json.load(fh)["entry"])


MOD = find_mod("_fix_prompt_errors")
failures = []


def check(name, cond, detail=""):
    print(("  ok   " if cond else "  FAIL ") + name
          + ((" -- " + str(detail)) if detail and not cond else ""))
    if not cond:
        failures.append(name)


# ---------------------------------------------------------------- pydantic の代わり
class FieldInfo:
    def __init__(self, annotation=None, default=..., description=None):
        self.annotation = annotation
        self.default = default
        self.description = description


def Field(default=..., description=None):
    return FieldInfo(None, default, description)


class FakeModel:
    """`create_model` の作る型の代わり。必須の欄が足りなければ作れない。入れ子は辞書のまま持つ。"""
    model_fields = {}

    def __init__(self, **data):
        missing = [name for name in self.model_fields if name not in data]
        extra = [name for name in data if name not in self.model_fields]
        if missing or extra:
            raise ValueError("missing {} extra {}".format(missing, extra))
        self.data = data

    @classmethod
    def model_validate(cls, data):
        return cls(**data)

    def model_dump(self):
        return json.loads(json.dumps(self.data, default=lambda v: v.value))


def create_model(*args, **fields):
    # 本物と同じく型の名前は位置だけで受ける（欄の `name` とぶつからない）
    name = args[0]
    table = {}
    for key, (annotation, default) in fields.items():
        description = getattr(default, "description", None)
        table[key] = FieldInfo(annotation, getattr(default, "default", default), description)
    return type(name, (FakeModel,), {"model_fields": table})


def str_enum(name, values):
    return enum.Enum(name, {v: v for v in values}, type=str)


KINDS = ["inn", "guild", "administrative_office", "medical_facility", "general_store",
         "blacksmith", "free"]
FACILITY_TYPE = str_enum("FacilityType", KINDS)


def game_area(kinds=KINDS):
    npc = create_model("NPC", name=(str, ...))
    facility = create_model("Facility", type=(str_enum("FacilityType", kinds), ...),
                            tier=(str, ...), name=(str, ...), concept=(str, ...),
                            description=(str, ...), owner=(npc, ...))
    loc = create_model("NonFunctionalLocation", type=(str_enum("LocType", ["location"]), ...),
                       name=(str, ...), description=(str, ...))
    ward = create_model("Ward", type=(str_enum("WardType", ["ward"]), ...), name=(str, ...),
                        description=(str, ...), locations=(List[Union[facility, loc]], ...))
    area = create_model("Area", settlement_structure_description=(str, ...), atomosphere=(str, ...),
                        settlement_structure=(List[Union[ward, facility, loc]], ...),
                        residents=(List[npc], ...), adventurers=(List[npc], ...))
    return area, facility


AREA, FACILITY = game_area()


def fac(kind, name):
    return {"type": kind, "tier": "basic", "name": name, "concept": "", "description": "d",
            "owner": {"name": "主"}}


def answer(missing=(), people=True):
    """最初の返事。区画「港」「市場」と最上位の場所1つ。`missing` の種類を抜く。"""
    port = [{"type": "location", "name": "桟橋", "description": "d"}]
    market = []
    for kind, where, name in (("inn", port, "潮風亭"), ("guild", market, "組合"),
                              ("administrative_office", market, "役場"),
                              ("medical_facility", port, "療所"), ("general_store", market, "雑貨屋")):
        if kind not in missing:
            where.append(fac(FACILITY_TYPE(kind), name))      # Enum の値で来ても数える
    return {"settlement_structure_description": "s", "atomosphere": "calm",
            "settlement_structure": [
                {"type": "ward", "name": "港", "description": "d", "locations": port},
                {"type": "ward", "name": "市場", "description": "d", "locations": market},
                {"type": "location", "name": "広場", "description": "d"}],
            "residents": [{"name": "住人"}] if people else [],
            "adventurers": [{"name": "冒険者"}] if people else []}


def slot(ward, name):
    return {"ward": ward, "tier": "basic", "name": name, "concept": "", "description": "d",
            "owner": {"name": "新しい主"}}


# ---------------------------------------------------------------- 偽の ctx
class FakeCtx:
    _mod = None

    def __init__(self):
        self.out_dir = OUT_DIR
        self.hooks = {}
        self.errors = []
        self.lines = []

    def out_path(self, *parts):
        path = os.path.join(self.out_dir, *parts)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        return path

    def logger(self, name, **kw):
        return ml.ModContext.logger(self, name, **kw)

    def log(self, message, level="INFO"):
        self.lines.append((level, message))

    def log_exc(self, message):
        self.errors.append(message)

    def resolve(self, target):
        return None, None, object()         # 別名はもう生えている

    def superseded(self):
        return False

    def wrap(self, target, **kw):
        def decorator(func):
            self.hooks[target] = func
            return func
        return decorator


def fresh():
    path = os.path.join(OUT_DIR, LOG_NAME)
    if os.path.exists(path):
        os.remove(path)
    spec = importlib.util.spec_from_file_location("prompt_errors_mod", MOD)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    ctx = FakeCtx()
    module.apply(ctx)
    return module, ctx


def read_log():
    path = os.path.join(OUT_DIR, LOG_NAME)
    if not os.path.exists(path):
        return []
    with io.open(path, encoding="utf-8") as fh:
        return [line.rstrip("\n") for line in fh if line.strip()]


PROMPT = ("エリアの詳細を生成してください。\n【手順】\n1. 作る。\n\n【構造の生成要素】\n"
          "- ロケーション種別: ロケーションには[ward, facility, location]の3つの大枠がある。\n"
          "- 必須facility: [inn, guild, administrative_office, medical_facility, general_store]の6種は、必ずそれぞれ一つずつが存在する。\n"
          "- 特殊facility \"free\": 独自の名物施設。\n"
          "【NPCの生成要素】\n- 名前: NPCの名前。\n"
          "【生成ルール】\n- ロケーション数が多い(7つ以上)場合、wardを用いて2~4つ程度をまとめながら記入する。\n\n"
          "【世界のデータ】\n- 概要")
MESSAGE = [{"role": "system", "content": "あなたはRPGのワールドマップジェネレーターです。"},
           {"role": "user", "content": PROMPT}]


# ---------------------------------------------------------------- 場面
#: 頼みごとの直しの見本（10-14 以降のゲームの頼みから抜いた文）。(頼み, 元, 直した後に入る文, 直した後に消える文, 当てた名前)
RULE_CASES = (
    ("quest_referee_event_resolve",
     "- effect: 効果を選択。失敗した結果何も起こらないものは'miss', 成功した結果何も起こらないのは'escaped'とすること。",
     "'no_effect'とすること", "'miss'", ["effect"]),
    ("quest_referee_with_free_action",
     "- プレイヤーが移動を試みた場合にはbattleよりもmove_and_battle, field_eventよりもmove_and_eventを実行する。",
     "'move_to'に移動先を記述した上でbattleやfield_eventを実行する", "move_and_battle", ["move"]),
    ("shop_item_generator_ordinary",
     "- value: アイテムの価値として「83」を指定する。この世界における最低が1で最高が70。例えば魔法の鱗はcreature_partであり、液状の生物の一部はliquid_materialであり、",
     "価値として「76」を指定する。この世界における最低が1で最高が76", "liquid_material",
     ["liquid", "value_request", "value_max"]),
    ("random_quest_generator", "【要件】\n- 難易度は85/70\n- trait: 自然言語で記述されるクリーチャーの特性。無いこともある。- drops:",
     "難易度は85/76（通常の上限76を超える。敵は上限の依頼よりさらに強い）", "無いこともある", ["difficulty", "trait"]),
    ("get_sentence_sought", "セリフは30文字以内かつ率直な文体で、具体的な休憩内容を含む。", "具体的な求刑内容", "休憩", ["typo"]),
    ("npc_detail_generater", "- usefulness: スキルの総合的な有用性を1～30で評価。\n", "書く欄は無い", "- usefulness:", ["usefulness"]),
    ("guard_npc_generator",
     "【出力要素】\n- category: 老若男女のカテゴリ。\n- description: 大まかな説明。どういう訓練を受けているとか、どういう立場だとか。\n"
     "- personality: 性格というか兵としての行動方針。いろいろ。\n- look_description: 外見を自然言語で記述。\n- image_generation_prompt: 英語で。",
     "- size: 体の大きさ。tiny・small・medium・large・huge のどれか。\n- image_generation_prompt", "personality",
     ["guard_category", "guard_description", "guard_fields"]),
    ("life_detail_setter",
     "- npcs: そのイベント時に登場するであろうNPC。社交では必ず一人以上、それ以外では空のリストでも構わない。",
     "- npc: そのイベント時に登場するであろうNPCを一人選ぶ", "空のリスト", ["npc"]),
    ("conversation_resolver", "「今日の現在ログ」に続きとして書き足す形で、会話の内容を要約して下さい。",
     "見出しを付けずに", "書き足す形", ["resolver"]),
    ("conversation_facilitator_after_retrieval",
     "- content_violation: 該当するか否か。一つでも該当がある場合はTrueを指定し、残りの出力は全て無とする。\n"
     "- statement: NPCとしての返答文。\n- call_free_action: 特殊な自由行動の開始の有無。",
     "type を casual_response、accepted を null、statement を空、call_free_action を false とする。\n"
     "- statement: NPCとしての返答文。\n- new_offer:", "全て無", ["violation", "new_offer"]),
    ("create_skill",
     "【スキル生成要素】\n  - `name`: 技や呪文の名前。\n  - `element`: スキルの属性（例：火、水、毒、闇など）。\n"
     "  - `max_uses`: 使用可能回数（1～10）。\n  - `description`: 効果を自然言語で説明してください。\n"
     "  - `effects`: ダメージ・回復・状態異常・バフ/デバフなどを、指定された構造で記述してください。\n",
     "  - 名前・属性・説明: 書く欄は無い。スキルリクエストのものがそのまま使われるので、効果と使用回数をそれに合わせる。\n"
     "  - `max_uses`: 使用可能回数（1～10）。\n  - `effects`: ダメージ・回復・状態異常を、", "`description`",
     ["skill_fields", "skill_buff"]),
    ("epilogue_pre_evaluator",
     "- difficulty: 一般的に見てそれがどの程度難しいか。\n- weight: 成功率や失敗率をそれぞれ0から10の尺度で評価する。",
     "very_hard・impossible のどれか。\n- weights: 大成功（critical_success）", "- weight:",
     ["epilogue_weights", "epilogue_difficulty"]),
    ("epilogue_generator", "1. 記述しろ。2. その結果としての人生全体の幸福度を選択しろ。",
     "miserable（惨め）から、生涯の記述に見合うものを選択しろ。", "幸福度を選択しろ", ["happiness"]),
    ("free_facility_generate",
     "   else a refusal text. A \"labor\" payout must be matched by a comparable cost (usually elapse of\n"
     "   the same number of units) — never hand out labor-scale money for free.\n"
     "   Every paid activity must DO something.\n"
     "プレイヤーに見える全てのテキスト(textのvalue・choiceのlabel・llmのprompt・farewell・memoryのtag)は日本語で書くこと。",
     "for free.\n   No step takes an item from the player.", "for free.\n   Every paid",
     ["appearance_language", "buyback"]),
    ("item_craft_generator",
     "現在の場所、プレイヤーの自然言語入力文を考慮し、…副産物があるべき場合はそれを'byproduct'に記述してください。\n"
     "- item_appearance: 英語で短い一文、画像生成プロンプトとして。(例:['A rusted longsword.'])\n",
     "プレイヤーの自然言語入力文を考慮し、…副産物があるべき場合はそれを'byproducts'に記述してください。\n"
     "- item_appearance: 英語で短い一文、画像生成プロンプトとして。(例:['A rusted longsword.'])\n"
     "- description: 品の説明を日本語で。\n- performance: 品の出来。", "現在の場所",
     ["byproducts", "craft_place", "craft_fields"]),
    ("skill_learn_manager", "- usefulness: スキルの総合的な有用性を1～30で評価。\n", "書く欄は無い", "- usefulness:", ["usefulness"]),
)


def scene_table():
    print("[表]")
    module, _ctx = fresh()
    for manager, before, present, absent, labels in RULE_CASES:
        after, hits = module.fix_text(manager, before)
        check("{} に当たる".format(manager), present in after and absent not in after and hits == labels,
              (hits, after))
    check("当てる頼みを絞った直しはほかの頼みに当たらない",
          module.fix_text("master_ai_npc_generater", "- category: 老若男女のカテゴリ。") == ("- category: 老若男女のカテゴリ。", []))
    check("どの頼みにも当てる直しはほかの頼みにも当たる",
          module.fix_text("master_ai_item_generate", "液状の生物の一部はliquid_materialであり")[1] == ["liquid"])
    check("上限以下の難易度は目盛りだけ直し、添え書きを付けない",
          module.fix_text("random_quest_generator", "難易度は76/70") == ("難易度は76/76", ["difficulty"]))
    check("上限以下の価値の指定は変えない",
          module.fix_text("shop_item_generator_ordinary", "価値として「60」を指定する。") == ("価値として「60」を指定する。", []))
    stage = lambda text: module.fix_text("settlement_quest_generator", text)[0]
    check("想定段階は難易度から（序盤・中盤・終盤）",
          stage("- 想定段階: ゲーム序盤\n- 難易度は18/70") == "- 想定段階: ゲーム序盤\n- 難易度は18/76"
          and stage("- 想定段階: ゲーム序盤\n- 難易度は19/70").startswith("- 想定段階: ゲーム中盤")
          and stage("- 想定段階: ゲーム序盤\n- 難易度は47/70").startswith("- 想定段階: ゲーム終盤"))
    check("想定段階は最初の難易度で決める（3件まとめての頼み）",
          stage("- 想定段階: ゲーム序盤\nquest_1:難易度は50/70 quest_2:難易度は10/70").startswith("- 想定段階: ゲーム終盤"))
    check("難易度が読めなければ想定段階の行を外す", stage("a\n- 想定段階: ゲーム序盤\nb") == "a\nb")
    original = [{"role": "system", "content": "x"}, {"role": "user", "content": RULE_CASES[0][1]}]
    copied, hits = module.fix_prompt("quest_referee_event_resolve", original)
    check("messages は写しで直す", hits == ["effect"] and copied is not original
          and original[1]["content"] == RULE_CASES[0][1] and copied[0] is original[0])
    check("MOD の頼みには当てない", module.fix_prompt("mod_shop_restock", [{"role": "user", "content":
          "この世界における最低が1で最高が70"}])[1] == [])
    check("当たらなければ同じリストを返す", module.fix_prompt("narrator", original)[0] is original)

def scene_rules():
    print("[頼み]")
    module, _ctx = fresh()
    after = module.rewrite_rules(PROMPT)
    check("まとまりを書き直す", after is not None and module.STRUCTURE_HEAD in after
          and "[ward, facility, location]" not in after and "6種" not in after)
    check("自由生成施設の段は元にあるときだけ", module.STRUCTURE_FREE in after
          and module.STRUCTURE_FREE not in module.rewrite_rules(PROMPT.replace("- 特殊facility \"free\": 独自の名物施設。\n", "")))
    check("【生成ルール】は1つにまとめて消える", "【生成ルール】" not in after and "2~4つ程度" not in after)
    check("ほかの節は残る", after.startswith("エリアの詳細を生成してください。\n【手順】\n1. 作る。\n\n")
          and "【NPCの生成要素】\n- 名前: NPCの名前。\n" in after and after.endswith("【世界のデータ】\n- 概要"))
    check("規模は値で書く", "village なら1つ、town なら2つ、city なら3つ" in after)
    check("まとまりが無ければ触らない", module.rewrite_rules("街を作って") is None
          and module.rewrite_rules(None) is None)
    rewritten, hit = module.rewrite_prompt(MESSAGE)
    check("messages は写しで書き換える", hit and rewritten[1]["content"] == after
          and MESSAGE[1]["content"] == PROMPT and rewritten[0] is MESSAGE[0])
    check("当たらなければ同じものを返す", module.rewrite_prompt([{"role": "user", "content": "x"}])[1] is False)


def scene_check():
    print("[確かめる]")
    module, _ctx = fresh()
    kinds = module.required_kinds(AREA)
    check("必須の5種", kinds == list(module.REQUIRED), kinds)
    area4, _f = game_area([k for k in KINDS if k != "medical_facility"])
    check("選べない種類は数えない", "medical_facility" not in module.required_kinds(area4))
    plain = create_model("Area", settlement_structure=(List[str], ...))
    check("施設の型が無ければ None", module.required_kinds(plain) is None)
    two = dict(answer(), adventurers=[{"name": "a"}, {"name": "b"}])
    check("そろっていれば欠けは無い", module.lacking(two, kinds) == ([], {}))
    check("区画の中の欠けを見つける", module.lacking(
        dict(answer(missing=("guild", "inn")), adventurers=two["adventurers"]), kinds) == (["inn", "guild"], {}))
    check("住人0人と冒険者2人未満を見つけ、3人までの数を出す",
          module.lacking(answer(people=False), kinds) == ([], {"residents": 3, "adventurers": 3})
          and module.lacking(answer(), kinds) == ([], {"adventurers": 2}))

    model = module.repair_model(AREA, ["guild"], {"adventurers": 2}, create_model, Field)
    check("頼み直しの型は欠けた分だけ", list(model.model_fields) == ["guild", "adventurers"],
          list(model.model_fields))
    one = model.model_fields["guild"].annotation
    check("施設は置く区画＋施設の欄（type を除く）", list(one.model_fields) == [
        "ward", "tier", "name", "concept", "description", "owner"], list(one.model_fields))
    check("説明が付く", model.model_fields["guild"].description == "ギルド（guild）")
    check("人物の並びは元の型の欄", model.model_fields["adventurers"].annotation
          is AREA.model_fields["adventurers"].annotation)
    text = module.repair_text(["guild"], {"adventurers": 2}, True)
    check("頼み直しの文", "guild（ギルド）" in text and "adventurers: 拠点に滞在する冒険者NPCをあと2人" in text
          and "concept は空文字列" in text and "【人物について決まっていること】" in text)
    check("自由生成施設なしなら concept に触れない", "concept" not in module.repair_text(["guild"], {}, False))


def scene_merge():
    print("[足す]")
    module, _ctx = fresh()
    order = module.facility_order(AREA)
    data = dict(answer(missing=("guild", "inn", "medical_facility"), people=False), adventurers=[{"name": "x"}])
    extra = {"guild": slot(" 市場 ", "新しい組合"), "inn": slot("無い区画", "新しい宿"),
             "medical_facility": slot("", "新しい療所"), "adventurers": [{"name": "a"}, {"name": "b"}]}
    report = module.merge(data, extra, ["guild", "inn", "medical_facility"], {"adventurers": 2, "residents": 3}, order)
    port, market, square = data["settlement_structure"][:3]
    check("名前の合う区画へ（前後の空白は見ない）", market["locations"][-1]["name"] == "新しい組合")
    check("合う区画が無ければ最上位へ", [x["name"] for x in data["settlement_structure"][3:]]
          == ["新しい宿", "新しい療所"])
    check("新しい施設の欄は元の並び", list(market["locations"][-1]) == order)
    check("人物は並びの後ろへ", [x["name"] for x in data["adventurers"]] == ["x", "a", "b"])
    check("記録", report == ["guild -> 市場", "inn -> top (no ward '無い区画')", "medical_facility -> top",
                             "adventurers +2", "residents: none added"], report)
    report2 = module.merge(answer(missing=("guild",)), {}, ["guild"], {}, order)
    check("答えに無ければ欠けたまま", report2 == ["guild: still missing"], report2)

    check("型は型で返す", type(module.shaped_like(AREA.model_validate(answer()), AREA, answer())) is AREA)
    check("辞書は辞書で返す", module.shaped_like({}, AREA, {"a": 1}) == {"a": 1})
    check("JSON は JSON で返す", json.loads(module.shaped_like("{}", AREA, {"a": FACILITY_TYPE("inn")}))
          == {"a": "inn"})


def scene_wrap():
    print("[包み]")
    module, ctx = fresh()
    check("自己確認が通る", not any(level == "ERROR" for level, _m in ctx.lines), ctx.lines)
    hook = ctx.hooks.get(SEND)
    check("世界生成の送り口を包む", hook is not None, list(ctx.hooks))
    sys.modules["pydantic"] = type(sys)("pydantic")
    sys.modules["pydantic"].create_model = create_model
    sys.modules["pydantic"].Field = Field
    sent = []
    first = {}

    full = [{"name": "冒険者1"}, {"name": "冒険者2"}, {"name": "冒険者3"}]

    def game_send(manager_name, message, structure, **kw):
        sent.append((manager_name, message, structure))
        if manager_name == module.REPAIR_NAME and structure is not AREA:
            return structure.model_validate({"guild": slot("市場", "あとの組合"),
                                             "adventurers": [{"name": "あとの冒険者"}]})
        return structure.model_validate(first["answer"])

    try:
        first["answer"] = dict(answer(), adventurers=full)
        result = hook(game_send, "create_settlement_detail", MESSAGE, AREA)
        check("そろっていれば1回だけ", len(sent) == 1 and type(result) is AREA)
        check("書き直した頼みで送る", module.STRUCTURE_HEAD in sent[0][1][1]["content"]
              and MESSAGE[1]["content"] == PROMPT and sent[0][2] is AREA)

        del sent[:]
        first["answer"] = dict(answer(missing=("guild",)), adventurers=[{"name": "最初の冒険者"}])
        result = hook(game_send, "create_settlement_detail", MESSAGE, AREA)
        check("欠けがあれば1回だけ頼み直す", len(sent) == 2 and sent[1][0] == module.REPAIR_NAME)
        name, message, structure = sent[1]
        check("頼み直しは欠けた分の型", list(structure.model_fields) == ["guild", "adventurers"])
        check("頼み直しは元の頼み＋最初の返事＋欠けの頼み", len(message) == 4
              and message[1]["content"] == sent[0][1][1]["content"]
              and message[2]["role"] == "assistant" and json.loads(message[2]["content"])["atomosphere"] == "calm"
              and message[3]["role"] == "user" and "guild（ギルド）" in message[3]["content"])
        data = result.model_dump()
        check("元の型で返り、足した分が入る", type(result) is AREA
              and data["settlement_structure"][1]["locations"][-1]["name"] == "あとの組合"
              and data["adventurers"] == [{"name": "最初の冒険者"}, {"name": "あとの冒険者"}])
        lines = read_log()
        check("記録が出る", any("checked: complete" in line for line in lines)
              and any("repaired [guild -> 市場; adventurers +1]" in line for line in lines), lines)

        del sent[:]

        def failing_send(manager_name, message, structure, **kw):
            sent.append((manager_name, message, structure))
            if manager_name == module.REPAIR_NAME:
                raise RuntimeError("400")
            return structure.model_validate(dict(answer(missing=("inn",)), adventurers=full))

        got = hook(failing_send, "create_settlement_detail", MESSAGE, AREA)
        check("頼み直しが失敗したら最初の返事を返す", type(got) is AREA and len(sent) == 2
              and module.lacking(got.model_dump(), module.REQUIRED)[0] == ["inn"])
        check("失敗を記録する", any("repair failed (RuntimeError: 400)" in line for line in read_log()))

        del sent[:]
        hook(game_send, "settlement_quest_generator", MESSAGE, AREA)
        hook(game_send, module.REPAIR_NAME, MESSAGE, AREA)
        check("ほかの頼み・頼み直しの頼みは街の確かめをしない", len(sent) == 2 and sent[0][1] is MESSAGE
              and sent[1][1] is MESSAGE)

        del sent[:]
        event = [{"role": "user", "content": RULE_CASES[0][1]}]
        other_hook = ctx.hooks.get("scripts.llm.llm_manager:send_request")
        check("ゲームの4つの部品の送り口を包む", other_hook is not None
              and "scripts.llm.llm_manager_battle:send_request_with_no_structure" in ctx.hooks, sorted(ctx.hooks))
        other_hook(game_send, "quest_referee_event_resolve", event, AREA)
        check("ほかの頼みにも表を当てて送る", "'no_effect'" in sent[-1][1][0]["content"]
              and event[0]["content"] == RULE_CASES[0][1])

        def nested(manager_name, message, structure, **kw):
            # 別名の包みが重なった形: 内側でもう一度同じ包みを通る
            return other_hook(game_send, manager_name, message, structure)
        del sent[:]
        other_hook(nested, "quest_referee_event_resolve", event, AREA)
        check("包みが重なっても1回だけ当てる", len(sent) == 1 and "'no_effect'" in sent[0][1][0]["content"])
        lines = read_log()
        check("記録は組み合わせごとに初回", sum("fixed quest_referee_event_resolve [effect] x1" in line
                                              for line in lines) == 1, lines)
    finally:
        sys.modules.pop("pydantic", None)


def main():
    scene_table()
    scene_rules()
    scene_check()
    scene_merge()
    scene_wrap()
    print()
    if failures:
        print("FAILED: {}".format(", ".join(failures)))
        return 1
    print("all ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
