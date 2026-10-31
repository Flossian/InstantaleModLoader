# -*- coding: utf-8 -*-
"""修正: ゲームの LLM への頼みの誤り（型との食い違い）を直し、街の必須施設と人物の欠けを補う（DOC.md）。

ゲームの頼みには、構造化出力の型と食い違う書き方がいくつもある（DOC.md §2・§4 の C）。
型に無い値を名指しする（'facility'・'miss'/'escaped'・move_and_battle・liquid_material）、
型に無い欄を書かせる（usefulness・衛兵の personality / look_description）、件数や値の上限が合わない
（「6種」と書いて5種・「/70」とゲームの上限 76）、決め打ちの文（「想定段階: ゲーム序盤」）、誤字など。
読み違えたモデル（Claude Haiku 5.5・思考なしなど）は、型に禁じられた値の代わりに別の値へ流れる。

この MOD はゲームの送り口（`llm.MANAGER_SEND_TARGETS` の4つの部品の `send_request*`）で、

  頼みの直し  頼みの名前ごとの表（`RULES`）で、食い違う文を型に合わせて書き直す（C）
  街の生成    `create_settlement_detail` は【構造の生成要素】と【生成ルール】を1つに書き直し（A）、
            返事の必須の5施設と住人（0人）・冒険者（2人未満）を数え、欠けていれば
            元の頼みと最初の返事に「欠けたものだけを書く」頼みを続け、欠けた分だけの型で1回だけ送る。
            返ってきた施設は名前の合う区画へ（無ければ最上位へ）、人物は並びへ足し、
            ゲームへは元の型の形で返す（B）

型はゲームのまま送る。頼みの文は送り口の中で型と一緒に使われるので（GAME.md §2.12）、
この1か所でローカルにもクラウドにも効く。MOD 自身の頼み（`mod_` で始まる名前）には当てない。
"""

import json
import re
import sys
import threading
import typing

from instantale_modloader import llm, value_limits

LOG_BASENAME = "prompt_errors.log"

MANAGER_NAME = "create_settlement_detail"
#: 頼み直しの頼みの名前（`output_data` にこの名前で残る。この MOD の包みは素通しする）。
REPAIR_NAME = "create_settlement_detail_repair"
#: 街の生成の送り口（`create_settlement_detail` はここから送られる）。
SEND_TARGET = "scripts.llm.llm_manager_world_generate:send_request"

#: 必須の5施設（プロンプトの「必須facility」）。型の選べる値に無いものは数えない。
REQUIRED = ("inn", "guild", "administrative_office", "medical_facility", "general_store")
LABELS = {"inn": "宿屋", "guild": "ギルド", "administrative_office": "役所",
          "medical_facility": "診療所", "general_store": "雑貨屋"}
#: 人物の並び -> (頼みの言い方, これ未満なら頼み直す, 頼み直しでここまで足す)。
#: ゲームの頼みは「住民NPCを何人か」「冒険者NPCを3名ほど」。
PEOPLE = {"residents": ("拠点内の住民NPC", 1, 3), "adventurers": ("拠点に滞在する冒険者NPC", 2, 3)}

STRUCTURE_FIELD = "settlement_structure"
WARD_FIELD = "ward"
WARD_NOTE = "この施設を置く区画（settlement_structure の ward）の name。区画に置かないなら空文字列"

# ================================================================== 頼み（A）

#: 【構造の生成要素】と【生成ルール】のまとまり（次の【見出し】の手前まで）。
STRUCTURE_BLOCK = re.compile(r"【構造の生成要素】.*?(?=【)", re.S)
RULES_BLOCK = re.compile(r"【生成ルール】.*?(?=【)", re.S)
#: 元の頼みが自由生成施設の段を持つか（`include_free_facility=True` の世界）。
FREE_MARK = "特殊facility"

#: 2つのまとまりを書き直した文。型（`Area`）の欄の名前と値に合わせて言い切る（DOC.md §4 の A）。
STRUCTURE_HEAD = (
    "【構造の生成要素】\n"
    "settlement_structure は拠点の中の場所の並び。要素は次の3つで、type に書く値で見分ける。\n"
    "- 区画: type は \"ward\"。拠点の一角（通り・地区など）。locations に、その区画の中にある施設とただの場所を並べる。"
    "区画の中に区画は置かない。\n"
    "- 施設: 宿屋や店など、プレイヤーが利用できる機能を持つ場所。type には下の施設の種類の値をそのまま書く"
    "（\"facility\" という値は無い）。owner に施設の主を書く。\n"
    "- ただの場所: type は \"location\"。「緑道」「廃墟」など機能を持たない、雰囲気や物語のための場所。好きなだけ置いてよい。\n"
    "施設の種類:\n"
    "- 必須の施設: inn（宿屋）・guild（ギルド）・administrative_office（役所）・medical_facility（診療所）・"
    "general_store（雑貨屋）の5種。どの拠点にも必ず1つずつ置く。\n"
    "- 選択の施設: blacksmith・specialty_shop・training_facility・underworld_office・slave_market・colosseum から、"
    "拠点の特色に合わせて選ぶ。数は settlement_size が village なら1つ、town なら2つ、city なら3つ。同じ種類を重ねてもよい。\n")
STRUCTURE_FREE = (
    "- 名物施設: type は \"free\"。上のどの種類にも当てはまらない独自の施設（例: 占いの館、大衆浴場、人形師の工房、"
    "風変わりな食堂など）。拠点の個性を出すため0〜1個置いてよい。concept に「プレイヤーがそこで何をできるか」を1〜2文で書く。"
    "free 以外の施設は concept を空文字列にする。\n")
STRUCTURE_TAIL = (
    "置き方:\n"
    "- 場所（施設とただの場所）が6つ以下なら、区画を使わず settlement_structure に直接並べてよい。\n"
    "- 7つ以上なら区画を作り、1つの区画に2〜4か所ずつまとめる。一部だけを区画にまとめるのも、全てを区画に分けるのも自由。\n"
    "- 施設も区画の中に置いてよい。settlement_structure に直接並べた場所は、拠点の入口から直接行ける。\n")


def rewrite_rules(text):
    """【構造の生成要素】と【生成ルール】を1つに書き直した本文。当たらなければ None。"""
    if not isinstance(text, str):
        return None
    found = STRUCTURE_BLOCK.search(text)
    if found is None:
        return None
    block = STRUCTURE_HEAD + (STRUCTURE_FREE if FREE_MARK in found.group(0) else "") + STRUCTURE_TAIL
    after = text[:found.start()] + block + text[found.end():]
    return RULES_BLOCK.sub("", after, count=1)


def rewrite_prompt(message):
    """書き直した messages の写し（`(写し, 当てたか)`）。元のリストは変えない。"""
    if not isinstance(message, list):
        return message, False
    copied = list(message)
    for index, turn in enumerate(copied):
        after = rewrite_rules(llm.content_of(turn))
        if after is not None:
            copied[index] = dict(turn, content=after)
            return copied, True
    return message, False


# ================================================================== 頼みごとの直し（C）

#: ゲームの難易度の上限（`QUEST_DIFFICULTY_VALUE_MAX`。GAME.md §2.9）。ゲームの頼みは「/70」「最高が70」と書く。
#: 76 を超えた難易度（`318_` で上限を上げたとき）もゲームはそのまま使うが、強くなるのは敵だけで、
#: 報酬・才能点・品の値段と強さは 76 の値で止まる（VERIFICATION.md §3.112）。
DIFFICULTY_MAX = 76
#: 76 を超えた難易度に添える文。
OVER_MAX_NOTE = "（通常の上限{}を超える。敵は上限の依頼よりさらに強い）".format(DIFFICULTY_MAX)
DIFFICULTY_70 = re.compile(r"難易度は(\d+)/70")
VALUE_REQUEST = re.compile(r"価値として「(\d+)」を指定")
#: 難易度の窓（GAME.md §2.9 の表。序盤の街 1〜18・中盤の街 19〜46・終盤の街 47〜76）。
STAGE_BOUNDS = ((19, "ゲーム序盤"), (47, "ゲーム中盤"))
STAGE_LATE = "ゲーム終盤"
#: ゲームが決め打ちで書く行（exe の定数。どの街の依頼でも「序盤」）。
STAGE_LINE = "- 想定段階: ゲーム序盤"
DIFFICULTY_IN_TEXT = re.compile(r"難易度は(\d+)/(?:70|76)")


def stage_of(text):
    """本文の最初の「難易度はN/70」から段階の名前。読めなければ None。"""
    found = DIFFICULTY_IN_TEXT.search(text)
    if found is None:
        return None
    value = int(found.group(1))
    for bound, name in STAGE_BOUNDS:
        if value < bound:
            return name
    return STAGE_LATE


def fix_difficulty(text):
    """「難易度はN/70」を上限 76 の目盛りに。76 を超えた値は、超えた分は敵だけが強いと添える。"""
    def one(found):
        value = int(found.group(1))
        note = OVER_MAX_NOTE if value > DIFFICULTY_MAX else ""
        return "難易度は{}/{}{}".format(value, DIFFICULTY_MAX, note)
    return DIFFICULTY_70.sub(one, text)


def fix_value_request(text):
    """「価値として「N」を指定」の N を、品の値段と強さに効く上限で切る。

    上限は窓口 `value_limits`（素は 76。ゲームは品の値段と強さを 76 の値で止める。
    `923_beyond_76` が入っていれば 100。TECH.md §3.3.26）。
    """
    def one(found):
        return "価値として「{}」を指定".format(min(int(found.group(1)), value_limits.value_max()))
    return VALUE_REQUEST.sub(one, text)


def fix_stage(text):
    """決め打ちの「想定段階: ゲーム序盤」を難易度からの段階に。難易度が読めなければ行ごと外す。"""
    if STAGE_LINE not in text:
        return text
    stage = stage_of(text)
    if stage is None:
        return text.replace(STAGE_LINE + "\n", "").replace(STAGE_LINE, "")
    return text.replace(STAGE_LINE, "- 想定段階: " + stage)


#: create_skill の型に無い3欄（名前・属性・説明）。名前の行と属性の行は続いている。
SKILL_NAME_ELEMENT = re.compile(r"( *)- `name`: 技や呪文の名前。\n *- `element`: [^\n]*\n")
SKILL_DESCRIPTION = re.compile(r" *- `description`: 効果を自然言語で説明してください。\n")


def fix_skill_fields(text):
    """create_skill の、型に無い名前・属性・説明の行を「書く欄は無い」の1行に。"""
    after = SKILL_NAME_ELEMENT.sub(
        r"\g<1>- 名前・属性・説明: 書く欄は無い。スキルリクエストのものがそのまま使われるので、効果と使用回数をそれに合わせる。\n",
        text)
    if after == text:
        return text
    return SKILL_DESCRIPTION.sub("", after)


ENEMY_MAKERS = ("random_quest_generator", "settlement_quest_generator", "colosseum_enemy_generator")
SKILL_MAKERS = ("npc_detail_generater", "master_ai_npc_generater", "skill_train_manager", "skill_learn_manager")
GUARD = ("guard_npc_generator",)
FACILITY_PROGRAM = ("free_facility_generate",)

#: (名前, 当てる頼み（None はゲームの頼み全部）, 種類, 探すもの, 置き換え)。上から順に当てる。
#: 種類は "text"（文字列の置き換え）・"regex"（正規表現）・"func"（本文を受けて返す関数）。
#: どれも 10-14 以降のゲームの頼みと型で確かめた食い違い（DOC.md §4 の C）。
RULES = (
    ("effect", ("quest_referee_event_resolve",), "text",
     "失敗した結果何も起こらないものは'miss', 成功した結果何も起こらないのは'escaped'とすること。",
     "成功しても失敗しても、結果として何も起こらないものは'no_effect'とすること。"),
    ("move", ("quest_referee_with_free_action",), "text",
     "プレイヤーが移動を試みた場合にはbattleよりもmove_and_battle, field_eventよりもmove_and_eventを実行する。",
     "プレイヤーが移動を試みた場合には'move_to'に移動先を記述した上でbattleやfield_eventを実行する。"),
    ("liquid", None, "text", "液状の生物の一部はliquid_materialであり", "液状の生物の一部もcreature_partであり"),
    ("stage", ("settlement_quest_generator",), "func", fix_stage, None),
    ("difficulty", None, "func", fix_difficulty, None),
    ("value_request", None, "func", fix_value_request, None),
    ("value_max", None, "text", "この世界における最低が1で最高が70", "この世界における最低が1で最高が{}".format(DIFFICULTY_MAX)),
    ("typo", ("get_sentence_sought",), "text", "具体的な休憩内容", "具体的な求刑内容"),
    ("trait", ENEMY_MAKERS, "regex", re.compile(r"(trait: [^\n。]*特性。)無いこともある。"),
     r"\g<1>必ず1つ書く（目立つ特性が無ければ控えめなものでよい）。"),
    ("usefulness", SKILL_MAKERS, "text", "- usefulness: スキルの総合的な有用性を1～30で評価。",
     "- 有用性: 書く欄は無い。ゲームが effects と max_uses から有用性（1〜30）を計算するので、"
     "それに見合う効果と使用回数にする。"),
    ("guard_category", GUARD, "text", "- category: 老若男女のカテゴリ。",
     "- category: monster・human_female・human_male のどれか。人の衛兵なら性別で選ぶ。"),
    ("guard_description", GUARD, "text", "どういう訓練を受けているとか、どういう立場だとか。",
     "どういう訓練を受けているとか、どういう立場だとか。"
     "兵としての行動方針（攻撃的だとか、チームワークを重視するとか）と、外見・服装もここに書く。"),
    ("guard_fields", GUARD, "regex", re.compile(r"- personality: [^\n]*\n- look_description: [^\n]*\n"),
     "- race: 種族。\n- size: 体の大きさ。tiny・small・medium・large・huge のどれか。\n"),
    ("npc", ("life_detail_setter",), "text",
     "- npcs: そのイベント時に登場するであろうNPC。社交では必ず一人以上、それ以外では空のリストでも構わない。",
     "- npc: そのイベント時に登場するであろうNPCを一人選ぶ。アイテム入手でも、関わりのあるNPCを一人選ぶ。"),
    ("resolver", ("conversation_resolver",), "text", "「今日の現在ログ」に続きとして書き足す形で、",
     "「今日の現在ログ」の続きになる文として、見出しを付けずに、"),
    # 以下は型つきの記録が無かった9種（返事の欄と exe の定数で確かめた。DOC.md §4 の「9種の洗い直し」）
    ("violation", ("conversation_facilitator_after_retrieval",), "text",
     "一つでも該当がある場合はTrueを指定し、残りの出力は全て無とする。",
     "一つでも該当がある場合はTrueを指定し、残りは type を casual_response、accepted を null、"
     "statement を空、call_free_action を false とする。"),
    ("new_offer", ("conversation_facilitator_after_retrieval",), "text", "- statement: NPCとしての返答文。\n",
     "- statement: NPCとしての返答文。\n"
     "- new_offer: 雇用の申し出が保留中で、プレイヤーが値段を交渉したときだけ使う。それ以外は null。\n"),
    ("skill_fields", ("create_skill",), "func", fix_skill_fields, None),
    ("skill_buff", ("create_skill",), "text", "ダメージ・回復・状態異常・バフ/デバフなどを、指定された構造で記述してください。",
     "ダメージ・回復・状態異常を、指定された構造で記述してください。"
     "バフ/デバフの枝は無いので、能力を上げ下げする技は状態異常（text_status）として書く。"),
    ("epilogue_weights", ("epilogue_pre_evaluator",), "text", "- weight: 成功率や失敗率をそれぞれ0から10の尺度で評価する。",
     "- weights: 大成功（critical_success）・少し成功（partial_success）・少し失敗（partial_failure）・"
     "失敗（failure）の起こりやすさを、それぞれ0から10の尺度で評価する。"),
    ("epilogue_difficulty", ("epilogue_pre_evaluator",), "text", "- difficulty: 一般的に見てそれがどの程度難しいか。",
     "- difficulty: 一般的に見てそれがどの程度難しいか。very_easy・easy・normal・hard・very_hard・impossible のどれか。"),
    ("happiness", ("epilogue_generator",), "text", "2. その結果としての人生全体の幸福度を選択しろ。",
     "2. その結果としての人生全体の幸福度を、fulfilled（満ち足りた）・content（満足）・ordinary（並）・"
     "unfulfilled（満たされない）・miserable（惨め）から、生涯の記述に見合うものを選択しろ。"),
    ("appearance_language", FACILITY_PROGRAM, "text",
     "プレイヤーに見える全てのテキスト(textのvalue・choiceのlabel・llmのprompt・farewell・memoryのtag)は日本語で書くこと。",
     "プレイヤーに見える全てのテキスト(textのvalue・choiceのlabel・llmのprompt・farewell・memoryのtag)は日本語で書くこと。\n"
     "ただし item_add の item_appearance だけは、絵の照合に使うので英語の短い一文で書くこと。"),
    ("buyback", FACILITY_PROGRAM, "text", "never hand out labor-scale money for free.\n",
     "never hand out labor-scale money for free.\n"
     "   No step takes an item from the player. If the concept is buying from the player, every labor\n"
     "   payout still needs an elapse of the same number of units.\n"
     "   Put every way out (choice options, input options) BEFORE the charge. There is no refund:\n"
     "   {price.X} is only ever charged.\n"),
    ("byproducts", ("item_craft_generator",), "text", "があるべき場合はそれを'byproduct'に記述してください。",
     "があるべき場合はそれを'byproducts'に記述してください。"),
    ("craft_place", ("item_craft_generator",), "text", "現在の場所、プレイヤーの自然言語入力文を考慮し",
     "プレイヤーの自然言語入力文を考慮し"),
    ("craft_fields", ("item_craft_generator",), "regex", re.compile(r"(- item_appearance: 英語で短い一文、画像生成プロンプトとして。[^\n]*)"),
     "\\g<1>\n- description: 品の説明を日本語で。\n"
     "- performance: 品の出来。weak・normal・strong のどれか。素材の質と組み合わせに見合うものを選ぶ。"),
)


def fix_text(manager, text):
    """1つの本文に表を当てる。`(直した本文, 当てた名前の並び)`。"""
    hits = []
    for label, managers, kind, find, to in RULES:
        if managers is not None and manager not in managers:
            continue
        if kind == "text":
            after = text.replace(find, to) if find in text else text
        elif kind == "regex":
            after = find.sub(to, text)
        else:
            after = find(text)
        if after != text:
            text = after
            hits.append(label)
    return text, hits


def fix_prompt(manager, message):
    """messages の写しに表を当てる（`(写し, 当てた名前)`）。当たらなければ元のリストを返す。

    MOD 自身の頼み（`mod_` で始まる名前）と、名前の読めない頼みには当てない。
    """
    if not isinstance(manager, str) or manager.startswith("mod_") or not isinstance(message, list):
        return message, []
    copied, hits = list(message), []
    for index, turn in enumerate(copied):
        text = llm.content_of(turn)
        if not text:
            continue
        after, found = fix_text(manager, text)
        if found:
            copied[index] = dict(turn, content=after)
            hits.extend(label for label in found if label not in hits)
    return (copied, hits) if hits else (message, [])


# ================================================================== 型

def value_of(item):
    """Enum の値はその値に、それ以外はそのまま。"""
    return getattr(item, "value", item)


def models_in(annotation):
    """注釈の中のモデルの型（`list[Union[...]]` をほどく）。並びは注釈の順。"""
    found, queue = [], [annotation]
    while queue:
        item = queue.pop(0)
        if isinstance(item, type) and isinstance(getattr(item, "model_fields", None), dict):
            found.append(item)
            continue
        queue.extend(typing.get_args(item))
    return found


def choices_of(annotation):
    """選べる値（Enum か Literal）。読めなければ None。"""
    if isinstance(annotation, type) and hasattr(annotation, "__members__"):
        return [value_of(member) for member in annotation]
    args = typing.get_args(annotation)
    if args and all(isinstance(arg, str) for arg in args):
        return list(args)
    return None


def facility_model(structure):
    """`settlement_structure` の要素のうち施設の型。見つからなければ None。"""
    info = (getattr(structure, "model_fields", None) or {}).get(STRUCTURE_FIELD)
    for member in models_in(getattr(info, "annotation", None)):
        names = member.model_fields
        if "type" in names and "owner" in names and "locations" not in names:
            return member
    return None


def required_kinds(structure):
    """数える必須の種類（型の選べる値にあるものだけ）。施設の型が無ければ None。"""
    facility = facility_model(structure)
    if facility is None:
        return None
    choices = choices_of(getattr(facility.model_fields["type"], "annotation", None))
    return [kind for kind in REQUIRED if choices is None or kind in choices]


def facility_order(structure):
    """施設の欄の並び（セーブの項目の並びを元の型と揃える）。"""
    facility = facility_model(structure)
    return list(facility.model_fields) if facility is not None else ["type"]


def repair_model(structure, kinds, people, create_model, field=None):
    """欠けた分だけを書かせる型。`kinds` は施設の種類、`people` は `{人物の並びの名前: 足す人数}`。"""

    def described(annotation, note):
        return (annotation, field(..., description=note)) if field is not None else (annotation, ...)

    fields = {}
    if kinds:
        facility = facility_model(structure)
        slot_fields = {WARD_FIELD: described(str, WARD_NOTE)}
        for name, info in facility.model_fields.items():
            if name != "type":
                slot_fields[name] = (info.annotation, info)
        slot = create_model("MissingFacility", **slot_fields)
        for kind in kinds:
            fields[kind] = described(slot, "{}（{}）".format(LABELS.get(kind, kind), kind))
    for name in people:
        info = structure.model_fields[name]
        fields[name] = (info.annotation, info)
    return create_model("SettlementRepair", **fields)


# ================================================================== 確かめる・足す（B）

def as_data(raw):
    """返事を辞書に（入れ子のモデルも辞書に）。読めなければ None。"""
    return llm.as_dict(raw)


def lacking(data, kinds):
    """`(欠けた施設の種類, {足りない人物の並び: 足す人数})`。"""
    present = set()
    for entry in data.get(STRUCTURE_FIELD) or []:
        if not isinstance(entry, dict):
            continue
        present.add(value_of(entry.get("type")))
        for child in entry.get("locations") or []:
            if isinstance(child, dict):
                present.add(value_of(child.get("type")))
    missing = [kind for kind in kinds if kind not in present]
    short = {}
    for name, (_label, minimum, target) in PEOPLE.items():
        have = data.get(name)
        count = len(have) if isinstance(have, list) else 0
        if name in data and count < minimum:
            short[name] = target - count
    return missing, short


def repair_text(kinds, people, free):
    """頼み直しの user の文。"""
    lines = ["直前に返した拠点のデータには、次のものが欠けている。欠けているものだけを、決められた形で書く。"]
    if kinds:
        lines.append("- 必須の施設: {}。どの拠点にも必ず1つずつ置く施設。".format(
            "・".join("{}（{}）".format(kind, LABELS.get(kind, kind)) for kind in kinds)))
        lines.append("  ward には置く区画の name を、直前のデータの区画から選んで書く。区画に置かないなら空文字列。")
        lines.append("  owner は【人物について決まっていること】にその施設の主があれば、それに合わせて書く。")
        lines.append("  拠点の雰囲気と、直前のデータの施設や人物と食い違わないように書く。")
        if free:
            lines.append("  concept は空文字列にする。")
    for name, count in people.items():
        lines.append("- {}: {}をあと{}人。直前のデータの人物とは別の人物にし、【NPCの生成要素】に従って書く。".format(
            name, PEOPLE[name][0], count))
    return "\n".join(lines)


def repair_message(message, data, kinds, people, free):
    """元の頼み＋最初の返事＋欠けた分の頼み。"""
    answer = json.dumps(data, ensure_ascii=False, default=value_of)
    return list(message) + [{"role": "assistant", "content": answer},
                            {"role": "user", "content": repair_text(kinds, people, free)}]


def merge(data, extra, kinds, people, order):
    """頼み直しの答え `extra` を `data` に足す。何をしたかの並びを返す。"""
    report = []
    items = data.get(STRUCTURE_FIELD)
    wards = {}
    for entry in items if isinstance(items, list) else []:
        if isinstance(entry, dict) and value_of(entry.get("type")) == "ward":
            wards.setdefault(str(entry.get("name") or "").strip(), entry)
    for kind in kinds:
        item = extra.get(kind)
        if not isinstance(item, dict) or not isinstance(items, list):
            report.append("{}: still missing".format(kind))
            continue
        entry = {"type": kind}
        for name in order:
            if name != "type" and name in item:
                entry[name] = item[name]
        ward_name = str(item.get(WARD_FIELD) or "").strip()
        ward = wards.get(ward_name)
        if ward is not None and isinstance(ward.get("locations"), list):
            ward["locations"].append(entry)
            report.append("{} -> {}".format(kind, ward_name))
        else:
            items.append(entry)
            report.append("{} -> top{}".format(kind, " (no ward {!r})".format(ward_name) if ward_name else ""))
    for name in people:
        added = extra.get(name)
        if isinstance(added, list) and added:
            data[name] = list(data.get(name) or []) + list(added)
            report.append("{} +{}".format(name, len(added)))
        else:
            report.append("{}: none added".format(name))
    return report


def shaped_like(raw, structure, data):
    """`data` を、最初の返事 `raw` と同じ形（モデル・辞書・JSON 文字列）にして返す。"""
    if isinstance(raw, dict):
        return data
    if isinstance(raw, str):
        return json.dumps(data, ensure_ascii=False, default=value_of)
    validate = getattr(structure, "model_validate", None)
    return validate(data) if callable(validate) else structure(**data)


# ================================================================== 包み

def apply(ctx):
    write = ctx.logger(LOG_BASENAME)
    #: id(元の型) -> (元の型, 種類)。
    kinds_of = {}
    warned = set()

    def warn_once(key, text):
        if key not in warned:
            warned.add(key)
            write("WARN " + text)

    def tools():
        pydantic = sys.modules.get("pydantic")
        create_model = getattr(pydantic, "create_model", None)
        if not callable(create_model):
            module = llm.manager()
            create_model = getattr(module, "create_model", None) if module is not None else None
        return create_model, getattr(pydantic, "Field", None)

    def kinds_for(structure):
        known = kinds_of.get(id(structure))
        if known is not None and known[0] is structure:
            return known[1]
        kinds = required_kinds(structure)
        kinds_of[id(structure)] = (structure, kinds)
        return kinds

    def repair(orig, args, kwargs, message, structure, raw, data, kinds):
        """欠けを確かめ、欠けていれば1回だけ頼み直して足す。ゲームへ返すものを返す。"""
        missing, short = lacking(data, kinds)
        if not missing and not short:
            write("checked: complete")
            return raw
        create_model, field = tools()
        if not callable(create_model):
            warn_once("create_model", "no create_model; cannot ask for {}".format(missing + list(short)))
            return raw
        free = "concept" in facility_order(structure)       # 自由生成施設ありの世界の型
        model = repair_model(structure, missing, short, create_model, field)
        extra_args, extra_kwargs = list(args), dict(kwargs)
        new_message = repair_message(message, data, missing, short, free)
        if extra_args:
            extra_args[0] = REPAIR_NAME
        else:
            extra_kwargs["manager_name"] = REPAIR_NAME
        if len(extra_args) > 1:
            extra_args[1] = new_message
        else:
            extra_kwargs["message"] = new_message
        if len(extra_args) > 2:
            extra_args[2] = model
        else:
            extra_kwargs["structure"] = model
        try:
            got = llm.as_dict(orig(*extra_args, **extra_kwargs))
        except Exception as exc:
            write("repair failed ({}: {}); kept the answer lacking {}".format(
                type(exc).__name__, exc, missing + list(short)))
            return raw
        if not isinstance(got, dict):
            write("repair unreadable; kept the answer lacking {}".format(missing + list(short)))
            return raw
        report = merge(data, got, missing, short, facility_order(structure))
        write("repaired [{}]".format("; ".join(report)))
        return shaped_like(raw, structure, data)

    #: 同じ呼び出しが別名の包みを重ねて通っても1回だけ当てる。
    pass_local = threading.local()
    #: (頼み, 当てた名前) -> 回数。記録は組み合わせごとに初回と 100 回ごと。
    fixed_counts = {}

    def note_fixed(manager, hits):
        key = (manager, tuple(hits))
        fixed_counts[key] = fixed_counts.get(key, 0) + 1
        count = fixed_counts[key]
        if count == 1 or count % 100 == 0:
            write("fixed {} [{}] x{}".format(manager, ", ".join(hits), count))

    def settle(orig, args, kwargs, structure, message):
        """街の生成: 書き直して送り、返事を確かめて欠けを頼み直す。"""
        try:
            new_message, hit = rewrite_prompt(message)
            if not hit:
                warn_once("rules", "the structure block was not found; the prompt is sent as is")
            new_args, new_kwargs = list(args), dict(kwargs)
            if len(new_args) > 1:
                new_args[1] = new_message
            else:
                new_kwargs["message"] = new_message
        except Exception:
            ctx.log_exc("prompt errors: cannot rewrite the settlement prompt; sending it as is")
            new_args, new_kwargs, new_message = list(args), dict(kwargs), message
        raw = orig(*new_args, **new_kwargs)
        try:
            kinds = kinds_for(structure)
            if kinds is None:
                warn_once("kinds", "no facility type in the structure; not checked")
                return raw
            data = as_data(raw)
            if data is None:
                write("unreadable answer; not checked")
                return raw
            return repair(orig, new_args, new_kwargs, new_message, structure, raw, data, kinds)
        except Exception:
            ctx.log_exc("prompt errors: cannot check the settlement answer; returning it as is")
            return raw

    def install_send(target, ns):
        @ctx.wrap(target, required=False)
        def send_request(orig, *args, **kwargs):
            if getattr(pass_local, "active", False):
                return orig(*args, **kwargs)
            pass_local.active = True
            try:
                manager_name = args[0] if args else kwargs.get("manager_name")
                message = args[1] if len(args) > 1 else kwargs.get("message")
                try:
                    new_message, hits = fix_prompt(manager_name, message)
                except Exception:
                    ctx.log_exc("prompt errors: cannot fix the prompt of {!r}; sending it as is".format(manager_name))
                    new_message, hits = message, []
                if hits:
                    note_fixed(manager_name, hits)
                    args, kwargs = list(args), dict(kwargs)
                    if len(args) > 1:
                        args[1] = new_message
                    else:
                        kwargs["message"] = new_message
                if manager_name == MANAGER_NAME and not ns:
                    structure = args[2] if len(args) > 2 else kwargs.get("structure")
                    return settle(orig, args, kwargs, structure, new_message)
                return orig(*args, **kwargs)
            finally:
                pass_local.active = False

    ns_of = dict(llm.MANAGER_SEND_TARGETS)
    llm.watch_aliases(ctx, [target for target, _ns in llm.MANAGER_SEND_TARGETS],
                      lambda target: install_send(target, ns_of[target]), label="prompt errors")

    # 注入した時点で、頼みの書き直しを確かめておく（実経路は新しい街に入るまで通らない）。
    sample = ("【構造の生成要素】\n- 特殊facility \"free\": x\n【NPCの生成要素】\n- 名前\n"
              "【生成ルール】\n- y\n\n【世界のデータ】\nz")
    after = rewrite_rules(sample)
    fixed, labels = fix_text("settlement_quest_generator",
                             "- 想定段階: ゲーム序盤\n難易度は30/70。液状の生物の一部はliquid_materialであり")
    ok = (after is not None and STRUCTURE_FREE in after and "【生成ルール】" not in after
          and after.endswith("【世界のデータ】\nz") and "【NPCの生成要素】\n- 名前\n" in after
          and fixed == "- 想定段階: ゲーム中盤\n難易度は30/76。液状の生物の一部もcreature_partであり"
          and labels == ["liquid", "stage", "difficulty"])
    if ok:
        ctx.log("prompt errors: {} rule(s) on the game's requests; settlement structure rules and repairs "
                "on {} (log {})".format(len(RULES), MANAGER_NAME, LOG_BASENAME))
    else:
        ctx.log("prompt errors: self-check failed", level="ERROR")
