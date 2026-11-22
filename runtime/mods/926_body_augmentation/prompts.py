# -*- coding: utf-8 -*-
"""LLM へ送る文と、返してもらう型。ゲームには触らない。

送るのは2種類だけ:

- 見立て（品10件までを1回で。控えがあれば送らない）: 体に適合する品か、どの部位か、改造の名前と説明、
  伸ばす能力値・下がる能力値・戦闘の代償の種類・特殊な働きの種類・仕立ての具合
- 世界観の判定（世界1つにつき1回）: 見た目の種類（機械・魔術・中立）・画面の題・違法の呼び方

量・料金・日数・失敗の見込みはコード（`rules.py`）が決める。LLM には選ぶことだけを任せる。
型に `Literal` は使わない（候補の縛りは受け取った側で検算する。TECH.md の LLM の節）。
"""

from . import rules

APPRAISE_MANAGER = "mod_body_augmentation_appraise"
THEME_MANAGER = "mod_body_augmentation_theme"

#: 返してもらう型（`llm.create_structure` に渡す）。真偽も番号も文字列で受ける。
#: 品ごとの型を `APPRAISE_LIST` の配列に入れて返してもらう。
APPRAISE_FIELDS = ("index", "usable", "part", "name", "description", "boost", "cost", "drawback", "special",
                   "finish", "reason")
APPRAISE_LIST = "items"
THEME_FIELDS = ("theme", "title", "illegal")

OVERVIEW_LIMIT = 1200


def _ability_lines():
    return "、".join("{}={}".format(key, name) for key, name in rules.ABILITY_NAMES.items())


#: 部位ごとの、向いている品の手がかり（部位が腕に偏らないように添える）。
PART_HINTS = {
    "head": "感覚や思考を補うもの",
    "arms": "握る・振るう・細かく動かすためのもの",
    "legs": "走る・跳ぶ・体を支えるためのもの",
    "body": "骨格・皮膚・守りになるもの",
    "organs": "液体・薬・循環や代謝に関わるもの",
    "sanity": "心・記憶・気力に関わるもの",
}


def _part_hint_lines():
    return ["  {}（{}）: {}".format(key, rules.PART_NAMES[key], PART_HINTS[key]) for key in rules.PARTS]


def _special_lines():
    return ["  {} のとき: {}".format(part, "、".join(
        "{}={}".format(kind, rules.SPECIAL_HINTS[kind]) for kind in rules.PART_SPECIALS[part]))
        for part in rules.PARTS]


def _item_line(number, info):
    return "{}. 名前: {} / 説明: {} / 種類: {}（{}） / レア度: {}".format(
        number, info.get("name") or "", info.get("description") or "", info.get("item_type") or "",
        info.get("detail") or "", info.get("rarity") or "")


def appraise_message(infos, overview, illegal):
    """見立ての頼み（`[{"role", "content"}]`）。品は `rules.BATCH_SIZE` 件まで、番号は 1 起点。"""
    who = "闇医者" if illegal else "医師"
    lines = [
        "あなたはこの世界の{}として、持ち込まれた品のうち、人の体に適合する品を見分けて見立てる。".format(who),
        "世界観に合う形で答える（機械の世界なら義肢や埋め込み、魔術の世界なら刻印や移植など）。",
        "",
        "【世界の概要】",
        (overview or "（不明）")[:OVERVIEW_LIMIT],
        "",
        "【品】",
    ] + [_item_line(number, info) for number, info in enumerate(infos, 1)] + [
        "",
        "【体に適合する品】",
        "- 生き物の部位・組織になる素材・埋め込める石や結晶・体に入れる薬液のように、"
        "そのまま、または施術の中で整えて、体の一部として使える品",
        "- 体に取り付ける・埋め込む・移植するために作られた部品",
        "別の用途のために仕上がった品（装身具・道具・武器・防具・書物・食べ物・日用品など）は、"
        "材料が体に役立ちそうでも適合しない（持ち主が素材から部品を作り直す）。",
        "種類の欄では決めない（体のために作られた部品が、防具や武器の種類になっていることがある）。",
        "",
        "【答え方】",
        "items に、品ごとに1つずつ返す。",
        "index: 品の番号。",
        "usable: 体に適合する品なら true、適合しない品は false。",
        "reason: usable が false のときだけ、理由を20字以内で。",
        "以下は usable が true のときだけ答える（false なら空の文字列）。",
        "part: 組み込む部位を1つ、英語の鍵で。品の性質にいちばん合う部位を選ぶ。"
        "硬い・重いというだけで腕にしない。",
    ] + _part_hint_lines() + [
        "name: 組み込んだ後の改造の名前（12字以内の日本語。品の名前をそのまま使わない）。",
        "description: どう組み込むかを1文（40字以内の日本語）。",
        "boost: 伸びる能力値を1つか2つ、カンマ区切りの英語の鍵で。{}".format(_ability_lines()),
        "  部位から決めつけず、品の名前と説明から読み取る。同じ部位でも、軍用品・市販品・闇の改造品・"
        "一点ものなど作りが違えば伸びる能力値も違ってよい。どの能力値も選べる。",
        "cost: その作りの弱みとして下がる能力値を1つ、英語の鍵で（boost と別のもの）。"
        "品の仕組みや作りから筋の通るものを選ぶ。見た目が変わるというだけで charisma にしない。どの能力値も選べる。",
        "drawback: その作りが戦いで弱みになる形を1つ、英語の鍵で。品の仕組みや作りから筋の通るものを選ぶ。"
        + "、".join("{}={}".format(kind, rules.DRAWBACK_HINTS[kind]) for kind in rules.DRAWBACKS),
        "special: 選んだ部位の候補から、品の性質に合う特殊な働きを1つ、英語の鍵で。",
    ] + _special_lines() + [
        "finish: 品の仕立ての具合。raw=手を入れていない素材、processed=加工した品、"
        "part=体に組み込むために作られた部品。",
        "数値は答えない（量は別に決まる）。",
    ]
    return [{"role": "user", "content": "\n".join(lines)}]


def theme_message(overview, area_text):
    """世界観の判定の頼み。"""
    lines = [
        "次の世界で、人の体に手を加える技術がどう呼ばれるかを決める。",
        "",
        "【世界の概要】",
        (overview or "（不明）")[:OVERVIEW_LIMIT],
    ]
    if area_text:
        lines += ["", "【いまいる土地】", area_text[:400]]
    lines += [
        "",
        "【答え方】",
        "theme: machine（機械・科学で体を作り替える世界）、magic（魔術・呪術・錬金で体を作り替える世界）、"
        "neutral（どちらとも言えない、または混ざっている世界）のどれか1つ。",
        "title: その技術の総称（8字以内の日本語）。世界に合い、誰にでも通じる一般的な語にする。"
        "ふだん使われない漢語や造語は避ける。",
        "illegal: 表で扱えない危険な施術の呼び方（4字以内の日本語）。title と同じく一般的な語にする。",
    ]
    return [{"role": "user", "content": "\n".join(lines)}]
