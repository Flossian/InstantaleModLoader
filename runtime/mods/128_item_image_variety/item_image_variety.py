# -*- coding: utf-8 -*-
"""アイテム画像の拡充: 偏りを散らし、素材に無い絵を足す。

素のゲームはアイテム生成時の外見文（item_appearance、英語一文）を埋め込み、
`Data/item_embeddings/<item_detail>.json` の画像埋め込みと突き合わせて最も似た1枚を選ぶ（`Embedding.get_similar_id.get_similar_embedding_id`）。
乱数が無い argmax なので、外見文が定型化している品種では同じ画像に集中する（実測: potion は 52個中35個が同じ1枚。
調査の全数値は VERIFICATION.md §3 の 128_ の項）。

仕様は2行に尽きる（版6でここへ立ち返って作り直した）:

- **同じアイテムの再入手は同じ絵**（同じ敵の同じドロップ・再ドロップ）
- **別のアイテムには別の絵**（別名なのに同じ見た目、を避ける）

これを1つの表で達成する。**「外見文 → 絵」の対応を世界ごとに
`state/item_image/<世界>.json` へ永続化**し、

- 対応表にある外見文 → その絵を返す（ログは `kept`）。
  ドロップの定義（名前と外見文）はクエスト生成時に作られてセーブに保存される
  ので、同じドロップは必ず同じ外見文で来る ― 名前の解決は要らない
- 初めての外見文 → 類似度が top1 の SIM_FLOOR 倍以上の候補を最大 TOP_K 件とり、
  **対応表でまだ使われていない（使用数最少の）絵**を割り当てる（同数なら
  類似度の高いほう。乱数は無い）。ログは `chose`

使用数は対応表の値から数えるので、別のカウントは持たない。過去の版が
`counts` として残した累計は `legacy_counts` として読み、使用数の下駄に
足し続ける（見せた履歴を捨てないため。新規には書かない）。

併せて、素の埋め込み辞書に無い305枚（document 105 / creature_part 171 /
mushroom 28 / accessory 1）の追加辞書 `data/<item_detail>.json` を実行時に
合成して選択肢に加える（キャプションを付けて同じ埋め込み空間で作ったもの。
作り方は build/README.md）。

もう1つ、素材に無い絵を足す。銃と爆発物・中世の武器と防具・魔法武器・防具と服と装身具。
どれも作者が画像生成 AI で作った一枚絵から起こした。
分類は同じ種類の元画像をまとめた単位（`categories.json` の `sources`。版10 で作者の指示により119分類を45分類（品の種類：ジャンル）にまとめ、防具：ファンタジーを中世・ファンタジー・ハイファンタジー・ダークファンタジーに分けて48分類）。
絵と辞書は分類ごとのフォルダに分ける（`images/<分類>/`・`data/<分類>/<品種>.json`）。
分類ごとの設定で使い方を決める（使わない／素の絵と混ぜる／別扱い）。使わない分類は辞書を読まない。分類のフォルダを消しても動く。
絵はゲームのフォルダには置かず、`images/<分類>/Assets/images/item_candidates_dark/<品種>/` に
持つ。ゲームは絵を `Assets/images/item_candidates_dark/<品種>/<鍵>.png` の相対パスで
引き、Kivy はそのパスが無いと探し先（`resource_add_path`）を順に見るので、
`images/<分類>/` を探し先に足せば同じ相対パスのまま表示される（作り方と出どころは
build/README.md）。
別扱いの分類の絵は、top1 がその分類のときだけ候補にし、そのときはその分類だけを候補にする。
素の絵も合う絵が無い外見文（top1 の類似度が低い大剣など）で、使用数最少の規則が
未使用の銃や制服を選ぶのを防ぐため。逆に銃の外見文で、まだ使っていない剣が選ばれることも無い。

セーブには何も足さない。選ばれた image_src がアイテムに書かれるのは素の
ゲームと同じ。履歴を消してやり直したいときは該当世界の
`state/item_image/<世界>.json` を消せばよい。

対象はゲーム側の関数1つだけ。`__main__` への別名の張り替えはローダに任せる:

    Embedding.get_similar_id:get_similar_embedding_id(query_text, item_sub_type)
"""

import os
import shutil
import sys
import threading

from instantale_modloader import patch
from instantale_modloader.state import (UNKNOWN_WORLD, WorldStore,
                                        world_filename, world_key)

# 候補を類似度上位から最大何件まで広げるか。
TOP_K = 10

# 候補に入れる類似度の下限（top1 の類似度に対する比）。
# クエリの近傍が密な品種では TOP_K 件まで開き、
# 急落する品種（clothing 等）では自動的に絞れる。
SIM_FLOOR = 0.8

# 同梱の追加辞書（素の辞書に無かった305枚。`data/<品種>.json`）を使うか。
# 切ると素の辞書だけで均しをかける（切り分け用）。足した絵の分類（下の IMAGE_SETS）は別の設定。
USE_EXTRA_DICT = True

# 部位をまたいで探す。ゲームが付けた部位に合う絵が無い外見文（包帯が道具、ダイナマイトが薬瓶で来るなど）は、
# 自分の部位でいちばん似た絵が CROSS_SLOT_BELOW 未満のときだけ、同じ 32x32 の部位（CROSS_SLOTS）も見て、
# そちらの最良が CROSS_SLOT_MIN 以上かつ自分 + CROSS_SLOT_MARGIN 以上ならその絵にする。
# 装備品は部位ごとに絵の寸法が違うのでまたがない。決まりの値は作者が選んだ「きつめ」（VERIFICATION.md §3.26）。
# 絵は頼まれた部位のフォルダに `xs.<元の部位>.<鍵>` の名で写す（`state/item_image_cross/`。探し先に足す）。
# 一度付いた絵は、設定を切っても対応表のとおり同じ絵のまま。
CROSS_SLOT = True
CROSS_SLOT_BELOW = 0.55
CROSS_SLOT_MIN = 0.65
CROSS_SLOT_MARGIN = 0.15
CROSS_SLOTS = ("food", "drink", "medicine", "potion", "plant", "mushroom", "tool", "relic", "scrap", "magical_material",
               "other_material", "creature_part", "document", "scroll", "liquid_material", "treasure", "metal", "gem",
               "ore", "accessory")
CROSS_PREFIX = "xs."
CROSS_DIRNAME = "item_image_cross"
IMAGE_REL = os.path.join("Assets", "images", "item_candidates_dark")
# 外した絵の付け替え先の表（`data/` の下。build/build_retired.py が書く）
RETIRED_FILE = "retired.json"

# 足した絵の分類ごとの使い方。分類は同じ種類の元画像をまとめた単位（`categories.json`）。
#   off       使わない（その分類の辞書を読まない）
#   mix       素の絵と同じ候補に混ぜる
#   separate  外見文にいちばん似た絵がその分類のときだけ、その分類の中から選ぶ
#             （ダークファンタジーの「麻のチュニック」に制服、を防ぐ。銃の外見文に剣、も起きない）
MODES = ("off", "mix", "separate")
SET_GUN_CYBER = "separate"
SET_GUN_CORP = "separate"
SET_GUN_FANTASY = "separate"
SET_GUN_FUTURE = "separate"
SET_GUN_MODERN = "separate"
SET_GUN_FLINTLOCK = "separate"
SET_GUN_SHOTGUN = "separate"
SET_GUN_SNIPER = "separate"
SET_GUN_EXPLOSIVE = "separate"
SET_WEAPON_MEDIEVAL = "mix"
SET_WEAPON_MAGIC = "mix"
SET_WEAPON_MAGIC_FUSION = "separate"
SET_WEAPON_SF = "separate"
SET_WEAPON_WA = "separate"
SET_WEAPON_MODERN = "separate"
SET_WEAPON_POSTAPOC = "separate"
SET_WEAR_MEDIEVAL = "mix"
SET_WEAR_FANTASY = "mix"
SET_WEAR_HIGHFANTASY = "mix"
SET_WEAR_DARKFANTASY = "mix"
SET_WEAR_EARLYMODERN = "mix"
SET_WEAR_ACCESSORY = "mix"
SET_WEAR_SCHOOL = "separate"
SET_WEAR_MODERN = "separate"
SET_WEAR_CYBER = "separate"
SET_WEAR_SF = "separate"
SET_WEAR_STEAMPUNK = "separate"
SET_WEAR_POSTAPOC = "separate"
SET_WEAR_WESTERN = "separate"
SET_WEAR_JAPANESE = "separate"
SET_WEAR_MILITARY = "separate"
SET_PART_SF = "separate"
SET_PART_STEAMPUNK = "separate"
SET_PART_MAGITECH = "mix"
SET_FOOD_RATION = "separate"
SET_FOOD_BASIC = "mix"
SET_FOOD_DISHES = "separate"
SET_MEDIC_FANTASY = "mix"
SET_MEDIC_MODERN = "separate"
SET_MATERIAL_MAGIC = "mix"
SET_MATERIAL_CREATURE = "mix"
SET_MATERIAL_MODERN = "separate"
SET_TOOL_FANTASY = "mix"
SET_TOOL_GENRE = "separate"
SET_TOOL_JAPANESE = "separate"
SET_TREASURE_FANTASY = "mix"
SET_TREASURE_MODERN = "separate"
SET_DOCUMENT_MODERN = "separate"
IMAGE_SETS = (
    ("gun_cyber", "SET_GUN_CYBER"),
    ("gun_corp", "SET_GUN_CORP"),
    ("gun_fantasy", "SET_GUN_FANTASY"),
    ("gun_future", "SET_GUN_FUTURE"),
    ("gun_modern", "SET_GUN_MODERN"),
    ("gun_flintlock", "SET_GUN_FLINTLOCK"),
    ("gun_shotgun", "SET_GUN_SHOTGUN"),
    ("gun_sniper", "SET_GUN_SNIPER"),
    ("gun_explosive", "SET_GUN_EXPLOSIVE"),
    ("weapon_medieval", "SET_WEAPON_MEDIEVAL"),
    ("weapon_magic", "SET_WEAPON_MAGIC"),
    ("weapon_magic_fusion", "SET_WEAPON_MAGIC_FUSION"),
    ("weapon_sf", "SET_WEAPON_SF"),
    ("weapon_wa", "SET_WEAPON_WA"),
    ("weapon_modern", "SET_WEAPON_MODERN"),
    ("weapon_postapoc", "SET_WEAPON_POSTAPOC"),
    ("wear_medieval", "SET_WEAR_MEDIEVAL"),
    ("wear_fantasy", "SET_WEAR_FANTASY"),
    ("wear_highfantasy", "SET_WEAR_HIGHFANTASY"),
    ("wear_darkfantasy", "SET_WEAR_DARKFANTASY"),
    ("wear_earlymodern", "SET_WEAR_EARLYMODERN"),
    ("wear_accessory", "SET_WEAR_ACCESSORY"),
    ("wear_school", "SET_WEAR_SCHOOL"),
    ("wear_modern", "SET_WEAR_MODERN"),
    ("wear_cyber", "SET_WEAR_CYBER"),
    ("wear_sf", "SET_WEAR_SF"),
    ("wear_steampunk", "SET_WEAR_STEAMPUNK"),
    ("wear_postapoc", "SET_WEAR_POSTAPOC"),
    ("wear_western", "SET_WEAR_WESTERN"),
    ("wear_japanese", "SET_WEAR_JAPANESE"),
    ("wear_military", "SET_WEAR_MILITARY"),
    ("part_sf", "SET_PART_SF"),
    ("part_steampunk", "SET_PART_STEAMPUNK"),
    ("part_magitech", "SET_PART_MAGITECH"),
    ("food_ration", "SET_FOOD_RATION"),
    ("food_basic", "SET_FOOD_BASIC"),
    ("food_dishes", "SET_FOOD_DISHES"),
    ("medic_fantasy", "SET_MEDIC_FANTASY"),
    ("medic_modern", "SET_MEDIC_MODERN"),
    ("material_magic", "SET_MATERIAL_MAGIC"),
    ("material_creature", "SET_MATERIAL_CREATURE"),
    ("material_modern", "SET_MATERIAL_MODERN"),
    ("tool_fantasy", "SET_TOOL_FANTASY"),
    ("tool_genre", "SET_TOOL_GENRE"),
    ("tool_japanese", "SET_TOOL_JAPANESE"),
    ("treasure_fantasy", "SET_TREASURE_FANTASY"),
    ("treasure_modern", "SET_TREASURE_MODERN"),
    ("document_modern", "SET_DOCUMENT_MODERN"),
)
# 版10 で分類を「品の種類：ジャンル」にまとめた（作者の指示。元画像1枚＝1分類の119分類 → 45分類）。旧い設定名で書かれたワールド個別設定を読むための表
OLD_SETTINGS = {
    "SET_CREATURE_FANG": "SET_MATERIAL_CREATURE",
    "SET_CREATURE_MATERIAL": "SET_MATERIAL_CREATURE",
    "SET_CREATURE_MUTANT": "SET_MATERIAL_CREATURE",
    "SET_FOOD_GENERAL": "SET_FOOD_BASIC",
    "SET_FOOD_MEAT": "SET_FOOD_DISHES",
    "SET_FOOD_SEAFOOD": "SET_FOOD_DISHES",
    "SET_FOOD_SEASIA": "SET_FOOD_DISHES",
    "SET_FOOD_SWEETS": "SET_FOOD_DISHES",
    "SET_FOOD_TRAVEL": "SET_FOOD_BASIC",
    "SET_FOOD_WESTERN": "SET_FOOD_DISHES",
    "SET_GUN_CORP_BIO": "SET_GUN_CORP",
    "SET_GUN_CORP_BULLPUP": "SET_GUN_CORP",
    "SET_GUN_CORP_CHEAP": "SET_GUN_CORP",
    "SET_GUN_CORP_HEAVY": "SET_GUN_CORP",
    "SET_GUN_CORP_LASER": "SET_GUN_CORP",
    "SET_GUN_CORP_LUXURY": "SET_GUN_CORP",
    "SET_GUN_CORP_MELEE": "SET_GUN_CORP",
    "SET_GUN_CORP_MILITARY": "SET_GUN_CORP",
    "SET_GUN_CORP_PRINT": "SET_GUN_CORP",
    "SET_GUN_CORP_RAIL": "SET_GUN_CORP",
    "SET_GUN_CORP_SPACE": "SET_GUN_CORP",
    "SET_GUN_CORP_WHITE": "SET_GUN_CORP",
    "SET_GUN_CORP_YAKUZA": "SET_GUN_CORP",
    "SET_GUN_CYBER2": "SET_GUN_CYBER",
    "SET_MAGIC_CORE": "SET_MATERIAL_MAGIC",
    "SET_MAGIC_CRYSTAL": "SET_MATERIAL_MAGIC",
    "SET_MAGIC_ESSENCE": "SET_MATERIAL_MAGIC",
    "SET_MAGIC_RELIC": "SET_MATERIAL_MAGIC",
    "SET_MAGIC_VESSEL": "SET_MATERIAL_MAGIC",
    "SET_MAG_CYBER": "SET_WEAPON_MAGIC_FUSION",
    "SET_MAG_EXTRA": "SET_WEAPON_MAGIC",
    "SET_MAG_ONEHANDED": "SET_WEAPON_MAGIC",
    "SET_MAG_POSTAPOC": "SET_WEAPON_MAGIC_FUSION",
    "SET_MAG_SF": "SET_WEAPON_MAGIC_FUSION",
    "SET_MAG_STAFF_POLEARM": "SET_WEAPON_MAGIC",
    "SET_MAG_STEAM": "SET_WEAPON_MAGIC_FUSION",
    "SET_MAG_TWOHANDED": "SET_WEAPON_MAGIC",
    "SET_MAG_WA": "SET_WEAPON_MAGIC_FUSION",
    "SET_MEDIC_POTION": "SET_MEDIC_FANTASY",
    "SET_MED_BLUNT_STAFF": "SET_WEAPON_MEDIEVAL",
    "SET_MED_BOW": "SET_WEAPON_MEDIEVAL",
    "SET_MED_ONEHANDED": "SET_WEAPON_MEDIEVAL",
    "SET_MED_POLEARM": "SET_WEAPON_MEDIEVAL",
    "SET_MED_SHIELD_ARMOR": "SET_WEAR_MEDIEVAL",
    "SET_MED_SHORT_THROWING": "SET_WEAPON_MEDIEVAL",
    "SET_MED_TWOHANDED": "SET_WEAPON_MEDIEVAL",
    "SET_MODERN_DOCUMENT": "SET_DOCUMENT_MODERN",
    "SET_MODERN_GEAR": "SET_WEAPON_MODERN",
    "SET_MODERN_INDUSTRIAL": "SET_MATERIAL_MODERN",
    "SET_MODERN_MEDICINE": "SET_MEDIC_MODERN",
    "SET_MODERN_TREASURE": "SET_TREASURE_MODERN",
    "SET_PART_BROKEN_BODY": "SET_PART_MAGITECH",
    "SET_PART_GEARS": "SET_PART_MAGITECH",
    "SET_PART_MAGITECH_CORE": "SET_PART_MAGITECH",
    "SET_SF_ARMOR": "SET_WEAR_SF",
    "SET_SF_MELEE": "SET_WEAPON_SF",
    "SET_SF_ONEHANDED": "SET_WEAPON_SF",
    "SET_SF_POLEARM": "SET_WEAPON_SF",
    "SET_SF_SHORT_THROWING": "SET_WEAPON_SF",
    "SET_SF_TWOHANDED": "SET_WEAPON_SF",
    "SET_TOOL_MODERN": "SET_TOOL_GENRE",
    "SET_TOOL_ODDITIES": "SET_TOOL_FANTASY",
    "SET_TOOL_POSTAPOC": "SET_TOOL_GENRE",
    "SET_TOOL_SF": "SET_TOOL_GENRE",
    "SET_TOOL_TRAVEL": "SET_TOOL_FANTASY",
    "SET_TOOL_WA": "SET_TOOL_GENRE",
    "SET_TOOL_WESTERN": "SET_TOOL_GENRE",
    "SET_TREASURE_COIN": "SET_TREASURE_FANTASY",
    "SET_TREASURE_CROWN": "SET_TREASURE_FANTASY",
    "SET_TREASURE_GEM": "SET_TREASURE_FANTASY",
    "SET_WASTELAND_GEAR": "SET_WEAPON_POSTAPOC",
    "SET_WA_ARMOR": "SET_WEAR_JAPANESE",
    "SET_WA_GOODS": "SET_TOOL_JAPANESE",
    "SET_WA_SWORDS": "SET_WEAPON_WA",
    "SET_WA_WEAPONS": "SET_WEAPON_WA",
    "SET_WEAR_CYBER1": "SET_WEAR_CYBER",
    "SET_WEAR_CYBER2": "SET_WEAR_CYBER",
    "SET_WEAR_DARKFANTASY1": "SET_WEAR_DARKFANTASY",
    "SET_WEAR_DARKFANTASY2": "SET_WEAR_DARKFANTASY",
    "SET_WEAR_EARLYMODERN_ARMOR": "SET_WEAR_EARLYMODERN",
    "SET_WEAR_EARLYMODERN_CLOTHES": "SET_WEAR_EARLYMODERN",
    "SET_WEAR_MEDIEVAL_ARMOR": "SET_WEAR_MEDIEVAL",
    "SET_WEAR_MEDIEVAL_CLOTHES": "SET_WEAR_MEDIEVAL",
    "SET_WEAR_MODERN_ARMOR": "SET_WEAR_MODERN",
    "SET_WEAR_MODERN_CASUAL": "SET_WEAR_MODERN",
    "SET_WEAR_MODERN_HARD": "SET_WEAR_MODERN",
    "SET_WEAR_POSTAPOC_ARMOR": "SET_WEAR_POSTAPOC",
    "SET_WEAR_SCHOOL_BOYS": "SET_WEAR_SCHOOL",
    "SET_WEAR_SCHOOL_CASUAL": "SET_WEAR_SCHOOL",
    "SET_WEAR_SCHOOL_COAT": "SET_WEAR_SCHOOL",
    "SET_WEAR_SCHOOL_DRESS": "SET_WEAR_SCHOOL",
    "SET_WEAR_SCHOOL_GIRLS": "SET_WEAR_SCHOOL",
    "SET_WEAR_SCHOOL_UNIFORM": "SET_WEAR_SCHOOL",
    "SET_WEAR_SF_ARMOR": "SET_WEAR_SF",
    "SET_WEAR_WA_ARMOR": "SET_WEAR_JAPANESE",
    "SET_WEAR_WESTERN_ARMOR": "SET_WEAR_WESTERN",
    "SET_WEAR_WIZARD": "SET_WEAR_FANTASY",
}

# 別扱い（separate）の分類を選んでよい類似度の下限。これに届かない外見文（日本語の文は埋め込みでは
# [UNK] になり、どの絵とも似ない）は素の側から選ぶ。銃の外見文の top1 は 0.55 以上（VERIFICATION.md §3.26）
SEPARATE_MIN_SIM = 0.4
# 別扱いの分類を選ぶには、素の側でいちばん似た絵よりこれだけ多く似ている必要がある。
# 「光る短剣」のように素の絵とほぼ同点の外見文は素の側に残す（ファンタジーの外見文に SF の絵が付かない）
SEPARATE_MARGIN = 0.05

# 世界のジャンルの重み（0〜1）。0 なら素の選び方と同じ。ワールド個別設定で、その世界のジャンルに重みを置く。
# 絵の類似度に「世界のジャンルの重み × 分類のジャンル内の重み × GENRE_BONUS」を足してから選ぶ
# （ジャンルが複数当たるときは一番大きい値）。分類のジャンル内の重みの既定は `genres.json`、
# 変えた値は `state/item_image_genres/weights.json`（設定画面の「ジャンル内の重み」タブ。既定と違う値だけ）
GENRE_MEDIEVAL = 0.0
GENRE_FANTASY = 0.0
GENRE_HIGHFANTASY = 0.0
GENRE_DARKFANTASY = 0.0
GENRE_WA = 0.0
GENRE_EARLYMODERN = 0.0
GENRE_MODERN = 0.0
GENRE_SF = 0.0
GENRE_CYBER = 0.0
GENRE_POSTAPOC = 0.0
GENRE_SETTINGS = (
    ("medieval", "GENRE_MEDIEVAL"),
    ("fantasy", "GENRE_FANTASY"),
    ("highfantasy", "GENRE_HIGHFANTASY"),
    ("darkfantasy", "GENRE_DARKFANTASY"),
    ("wa", "GENRE_WA"),
    ("earlymodern", "GENRE_EARLYMODERN"),
    ("modern", "GENRE_MODERN"),
    ("sf", "GENRE_SF"),
    ("cyber", "GENRE_CYBER"),
    ("postapoc", "GENRE_POSTAPOC"),
)
# 重み 1 のジャンルの絵に足す類似度の上限（試算で 0.1〜0.15 が効いた。VERIFICATION.md §3.26）
GENRE_BONUS = 0.15
GENRE_WEIGHTS_DIRNAME = "item_image_genres"
GENRE_WEIGHTS_FILE = "weights.json"

# ワールド個別の設定の控え（`state/item_image_variety/<世界>.json`。一括設定と違う項目だけ）。
# 名前は設定画面の道具（`modtool.state_dirname`）が作る名前と同じ。TECH.md §3.12.1
SETTINGS_DIRNAME = "item_image_variety"
SETTINGS_STORE_ATTR = "_iml_item_image_world_settings"
# 世界ごとに上書きできる設定（mod.json の settings と同じ並び）
SETTING_NAMES = (("TOP_K", "SIM_FLOOR", "USE_EXTRA_DICT", "CROSS_SLOT")
                 + tuple(setting for _genre, setting in GENRE_SETTINGS) + ("GENRE_BONUS",)
                 + tuple(setting for _name, setting in IMAGE_SETS))


def read_genre_weights(mod_dir, user_path):
    """`(素の絵の重み, {分類: {ジャンル: 重み}})`。既定（`genres.json`）に、変えた値（user_path）を重ねる。
    読めない・形の違う値は既定のまま。ジャンルは `GENRE_SETTINGS` にあるものだけ。"""
    import json
    known = {genre for genre, _setting in GENRE_SETTINGS}

    def clean(table):
        out = {}
        if isinstance(table, dict):
            for genre, value in table.items():
                if genre in known and isinstance(value, (int, float)) and not isinstance(value, bool):
                    out[genre] = max(0.0, min(1.0, float(value)))
        return out
    try:
        with open(os.path.join(mod_dir, "genres.json"), encoding="utf-8") as fh:
            base = json.load(fh)
    except (OSError, ValueError):
        base = {}
    stock = clean(base.get("stock"))
    weights = {cat: clean(table) for cat, table in (base.get("weights") or {}).items()}
    try:
        with open(user_path, encoding="utf-8") as fh:
            user = json.load(fh)
    except (OSError, ValueError):
        user = {}
    if isinstance(user, dict):
        if "stock" in user:
            stock.update(clean(user.get("stock")))
        for cat, table in (user.get("weights") or {}).items():
            if cat in weights:
                weights[cat].update(clean(table))
    return stock, weights

EMBEDDING_MODULE = "Embedding.get_similar_id"

# ゲームのイベントの判定（field_event_evaluator / quest_referee_event_resolve）は、入手する品の外見文の
# 書き方を指示していないので、本文と同じ日本語で返る。日本語の外見文は埋め込みで [UNK] だけになり、
# 品と関係ない絵が付く。効果の get_item の行に、店の品揃えの頼みと同じ書き方の指示を足す（VERIFICATION.md §3.26）
EVENT_ITEM_MARK = "- get_item: プレイヤーが何らかのアイテムを入手する。"
EVENT_ITEM_NOTE = ("入手するアイテムの item_appearance は英語で短い一文、画像の選択に使う。"
                   "(例:['A rusted longsword with a chipped blade and worn leather grip.'])")


def event_item_note(texts, _site=None):
    """`wrap_outgoing` の書き換え。目印の行の後ろに指示を1回だけ足す。変えないなら None。"""
    out = [t.replace(EVENT_ITEM_MARK, EVENT_ITEM_MARK + EVENT_ITEM_NOTE, 1)
           if EVENT_ITEM_MARK in t and EVENT_ITEM_NOTE not in t else t for t in texts]
    return out if out != list(texts) else None


def mode_of(setting):
    """分類の使い方。知らない値は使わない扱い（手で直した控えの綴り違いで、意図しない絵が混ざらないように）。"""
    value = globals().get(setting)
    return value if value in MODES else "off"


def apply(ctx):
    # 分類の絵の探し先。使わない分類も足す（探し先に足すだけでは絵を読まない。
    # 既にその分類の絵が付いている品の表示を保つため）。フォルダが無い分類は足さない。
    # 選定の関数を待つより先に足す（ロード直後の所持品の表示に間に合わせる）。重ねて足しても1つ。
    # 部位をまたいだ絵の写しの置き場も同じく足す（前の起動で写した絵の表示を保つ）
    cross_root = ctx.state_path(CROSS_DIRNAME)
    try:
        from kivy.resources import resource_add_path
        for name, _flag in IMAGE_SETS:
            folder = os.path.join(ctx.mod_dir, "images", name)
            if os.path.isdir(folder):
                resource_add_path(folder)
        os.makedirs(cross_root, exist_ok=True)
        resource_add_path(cross_root)
    except Exception as exc:
        ctx.log("item_image_variety: cannot add the image path: {!r}".format(exc))

    # イベントで入手する品の外見文を英語で頼む（選定の関数とは別の経路なので、待たずに先に掛ける）
    from instantale_modloader.llm import wrap_outgoing
    wrap_outgoing(ctx, event_item_note, label="item image: event appearance")

    # 外した絵の付け替え。描き直しで外した鍵の絵は消えるので、その絵の付いた品（セーブに相対パスで残る）は
    # 白い枠になる。品が作られるとき（セーブの読み込みも通る）に絵が見つからなければ、
    # `data/retired.json`（build_retired.py が書く。外した鍵 → 同じ品種のいちばん近い今の絵）で付け替える。
    # 書き換えた `image_src` は次の保存でセーブに残る。選定の関数を待たずに掛ける（ロード直後の所持品に間に合わせる）
    retired = ctx.read_json(os.path.join(ctx.mod_dir, "data", RETIRED_FILE), default=None)
    retired = retired if isinstance(retired, dict) else {}
    retire_log = ctx.logger("item_image.log")
    seen = {}

    def visible(src):
        """絵が引けるか（ゲームのフォルダか探し先に在るか）。パスごとに1回だけ確かめる。"""
        if src not in seen:
            found = os.path.isfile(src)
            if not found:
                try:
                    from kivy.resources import resource_find
                    found = resource_find(src) is not None
                except Exception:
                    found = False
            seen[src] = found
        return seen[src]

    def replacement(src):
        """見つからない絵のパスの付け替え先（同じ書き方のパス）。表に無ければ None。"""
        norm = src.replace("\\", "/")
        marker = IMAGE_REL.replace(os.sep, "/") + "/"
        at = norm.find(marker)
        if at < 0 or not norm.endswith(".png"):
            return None
        slot, _, key = norm[at + len(marker):-4].partition("/")
        if key.startswith(CROSS_PREFIX):
            # 部位をまたいだ絵の写し `xs.<元の部位>.<鍵>`。元の部位の今の絵を直に指す
            slot, _, key = key[len(CROSS_PREFIX):].partition(".")
        new = retired.get("{}/{}".format(slot, key))
        return None if new is None else "{}{}/{}.png".format(norm[:at + len(marker)], slot, new)

    @ctx.wrap("scripts.items:Item.__init__", required=False, safe=True)
    def item_init(orig, self, *args, **kwargs):
        result = orig(self, *args, **kwargs)
        src = getattr(self, "image_src", None)
        if isinstance(src, str) and IMAGE_REL.replace(os.sep, "/") in src.replace("\\", "/") and not visible(src):
            new = replacement(src)
            if new is not None and visible(new):
                self.image_src = new
                retire_log("retired: {} -> {}".format(src, new))
            elif seen.get(("missing", src)) is None:
                seen[("missing", src)] = True
                retire_log("retired: no image for {}".format(src))
        return result

    # 無ければローダの保留に積んで降りる（`patch.await_module`）。来たらローダが当て直す。
    # `sys.modules` を見て自分で降りると保留に載らず、ほかに保留が無い構成ではその起動で一度も当たらない。
    emb_mod = patch.await_module(EMBEDDING_MODULE, "text_to_embedding", "get_similar_embedding_id")
    if emb_mod is None:
        ctx.log("{} not loaded yet; waiting for it".format(EMBEDDING_MODULE))
        return

    log = ctx.logger("item_image.log")
    # ctx.mod_dir / ctx.state_path は apply() の外を当てにしない。ここで控える。
    mod_dir = ctx.mod_dir
    state_dir = ctx.state_path("item_image")
    # 品物の生成は LLM のワーカースレッドから来る（同時に2本まで観測済み）。
    # 表の読み書きも控えの書き出しもここで直列化する。
    # `ctx.write_json` は隣に `<名前>.tmp` を書いてから差し替える形なので、
    # 2本が同時に通ると **片方が書きかけの tmp をもう片方が置き換える**。
    # 控えが壊れ、次の `read_json` で丸ごと失われる。
    lock = threading.Lock()
    state = {
        "world": None,   # images を読んだ世界（world_key）。替わったら読み直す
        "file": None,    # その世界の控え（state/item_image/<世界>.json）
        "images": {},    # (sub_type, 外見文) -> 絵。この表が仕様の本体
        "legacy": {},    # (sub_type, 絵) -> 旧版が数えた累計。使用数の下駄
        "usage": {},     # (sub_type, 絵) -> 使用数（images の値 + legacy から導出）
        "pools": {},     # (sub_type, 分類の使い方の署名) -> (keys, 正規化済み行列, キー集合, 側) のキャッシュ
    }

    # ワールド個別の設定。控えのある世界ではその値を、無い世界では一括設定（ローダが注入した値）を使う。
    # 置き場は `sys`（当て直しで作り直した空の器を握らないため。`314_` と同じ）。
    settings_store = getattr(sys, SETTINGS_STORE_ATTR, None)
    if not isinstance(settings_store, WorldStore):
        settings_store = WorldStore(ctx, SETTINGS_DIRNAME, default=dict, write=log)
        setattr(sys, SETTINGS_STORE_ATTR, settings_store)
    else:
        settings_store.rebind(ctx, log)
    # 注入のたびにモジュールは作り直され、一括設定を注入してからここへ来る。
    # だからここで控えた値がそのまま一括設定（`sys` には置かない。TECH.md §3.12.1）
    base_settings = {name: globals()[name] for name in SETTING_NAMES}
    active_settings = [None]

    def refresh_settings(world):
        """その世界の個別設定を一括設定に重ねて、モジュールの値にする。型の違う値は使わない。"""
        record = settings_store.load(world, fresh=True) if world != UNKNOWN_WORLD else {}
        values = {}
        if isinstance(record, dict):
            # 版9 までの分類ごとの設定名（OLD_SETTINGS）で書かれた値は、まとめた分類の設定として読む（新しい名前が先）
            record = dict(record)
            for old, new in OLD_SETTINGS.items():
                if old in record and new not in record:
                    record[new] = record[old]
        for name in SETTING_NAMES:
            value = record.get(name) if isinstance(record, dict) else None
            values[name] = value if value is not None and type(value) is type(base_settings[name]) \
                else base_settings[name]
        signature = (world, tuple(sorted(values.items())))
        if signature != active_settings[0]:
            globals().update(values)
            active_settings[0] = signature
            if any(values[n] != base_settings[n] for n in SETTING_NAMES):
                log("settings {!r}: {}".format(world, ", ".join(
                    "{}={}".format(n, values[n]) for n in SETTING_NAMES if values[n] != base_settings[n])))

    def ensure_world():
        """世界が替わっていたら、その世界の対応表を読み直す。個別設定は毎回見る（変わっていれば読み直す）。"""
        app = getattr(sys.modules.get("__main__"), "instantale_app", None)
        world = world_key(app) if app is not None else UNKNOWN_WORLD
        refresh_settings(world)
        if world == state["world"]:
            return
        if world == UNKNOWN_WORLD:
            # 世界名が読めない窓（読み込み中・新規作成中）。実在の世界として
            # 扱うと通った全世界の対応が1ファイルに混ざるので、控えを持たずに
            # その場をしのぐ（`902_city_case` と同じ判断）。
            state.update(world=world, file=None, images={}, legacy={}, usage={})
            log("seed: the world name is unreadable; not keeping the table for now")
            return
        path = os.path.join(state_dir, world_filename(world, ".json"))
        images = {}
        legacy = {}
        stored = ctx.read_json(path, default=None)
        if isinstance(stored, dict):
            for label, key in (stored.get("images") or {}).items():
                folder, _, text = label.partition("/")
                if folder and text and isinstance(key, str) and key:
                    images[(folder, text)] = key
            # 旧版（〜版5）の counts は「過去に見せた累計」。捨てずに下駄として残す。
            for label, n in (stored.get("legacy_counts")
                             or stored.get("counts") or {}).items():
                folder, _, key = label.partition("/")
                if folder and key and isinstance(n, int) and n > 0:
                    legacy[(folder, key)] = n
        usage = dict(legacy)
        for (folder, _text), key in images.items():
            spot = (folder, key)
            usage[spot] = usage.get(spot, 0) + 1
            if key.startswith(CROSS_PREFIX):
                # 写しが消えていたら置き直す（ロードしたセーブの品は選定を通らずに絵を引く）
                place_cross(folder, key)
        state.update(world=world, file=path, images=images,
                     legacy=legacy, usage=usage)
        log("seed {!r}: {} texts mapped, legacy {} kinds".format(
            world, len(images), len(legacy)))

    def persist():
        """対応表を書く。失敗はローダが記録する（ゲームには流れない）。"""
        if not state["file"]:
            return
        data = {"images": {"{}/{}".format(folder, text): key
                           for (folder, text), key in sorted(state["images"].items())}}
        if state["legacy"]:
            data["legacy_counts"] = {"{}/{}".format(folder, key): n
                                     for (folder, key), n in sorted(state["legacy"].items())}
        ctx.write_json(state["file"], data)

    genre_path = os.path.join(ctx.state_path(GENRE_WEIGHTS_DIRNAME), GENRE_WEIGHTS_FILE)
    genre_cache = {"stamp": None, "table": ({}, {}), "bonus": {}}

    def genre_table():
        """分類のジャンル内の重み。変えた値のファイルが書き換わったら読み直す（設定画面で保存したらその場で効く）。"""
        try:
            stamp = os.path.getmtime(genre_path)
        except OSError:
            stamp = None
        if genre_cache["stamp"] != (stamp,):
            genre_cache["stamp"] = (stamp,)
            genre_cache["table"] = read_genre_weights(mod_dir, genre_path)
            genre_cache["bonus"] = {}
        return genre_cache["table"]

    def genre_bonus(pool_key, cats):
        """鍵ごとに足す類似度（torch のベクトル）。世界のジャンルの重みが全部 0 なら None（素の選び方）。"""
        world = {genre: globals()[setting] for genre, setting in GENRE_SETTINGS}
        if GENRE_BONUS <= 0 or not any(w > 0 for w in world.values()):
            return None
        stock, weights = genre_table()
        signature = (pool_key, GENRE_BONUS, tuple(sorted(world.items())))
        # 引き直さず手元の値を返す（間に別のスレッドの `genre_table` がキャッシュを空にすると KeyError になる）
        bonus = genre_cache["bonus"].get(signature)
        if bonus is None:
            import torch

            def bonus_of(cat):
                table = stock if cat == "" else weights.get(cat, {})
                return GENRE_BONUS * max([world.get(g, 0.0) * w for g, w in table.items()] or [0.0])
            per_cat = {cat: bonus_of(cat) for cat in set(cats)}
            bonus = genre_cache["bonus"][signature] = torch.tensor([per_cat[c] for c in cats], dtype=torch.float32)
        return bonus

    def load_pool(sub_type):
        """素の辞書と同梱の追加辞書を合成して (keys, tensor, キー集合, 側, 分類, 署名) を返す。
        側は鍵ごとに、別扱い（separate）の分類ならその分類の名前、それ以外（素の絵と mix の分類）は空。
        分類は鍵ごとの分類の名前（素の絵と同梱の追加辞書は空。ジャンルの重みを引く）。"""
        # 分類の使い方は世界ごとに変わりうるので、署名ごとに持つ
        modes = tuple(mode_of(setting) for _n, setting in IMAGE_SETS)
        cache_key = (sub_type, USE_EXTRA_DICT) + modes
        if cache_key in state["pools"]:
            return state["pools"][cache_key]
        import json

        import torch

        # ゲーム自身と同じ相対パス（カレントはゲーム本体のフォルダ）。
        base_path = os.path.join("Data", "item_embeddings", sub_type + ".json")
        if not os.path.isfile(base_path):
            state["pools"][cache_key] = None
            return None
        with open(base_path, encoding="utf-8") as fh:
            table = json.load(fh)
        n_base = len(table)
        side = {}
        category = {}
        extra_paths = []
        if USE_EXTRA_DICT:
            extra_paths.append((os.path.join(mod_dir, "data", sub_type + ".json"), "", ""))
        for (name, _setting), mode in zip(IMAGE_SETS, modes):
            if mode != "off":
                extra_paths.append((os.path.join(mod_dir, "data", name, sub_type + ".json"),
                                    name if mode == "separate" else "", name))
        for extra_path, extra_side, extra_cat in extra_paths:
            if not os.path.isfile(extra_path):
                continue
            extra = ctx.read_json(extra_path, default=None)
            if isinstance(extra, dict) and "index" in extra and "vectors" in extra:
                # 同じキャプションのベクトルを1つにまとめた形（build_wear.py）
                vectors = extra["vectors"]
                extra = {key: vectors[i] for key, i in extra["index"].items()}
            if extra:
                for key, vec in extra.items():
                    if key not in table:     # 素の辞書が同じキーを得たら素を優先
                        table[key] = vec
                        if extra_side:
                            side[key] = extra_side
                        if extra_cat:
                            category[key] = extra_cat
        keys = list(table)
        matrix = torch.tensor([table[k] for k in keys], dtype=torch.float32)
        matrix = matrix / matrix.norm(dim=1, keepdim=True).clamp_min(1e-12)
        pool = (keys, matrix, frozenset(keys), [side.get(k, "") for k in keys],
                [category.get(k, "") for k in keys], cache_key)
        state["pools"][cache_key] = pool
        log("pool {}: base={} merged={}".format(sub_type, n_base, len(keys)))
        return pool

    def rank(pool, query):
        """辞書1つの中で候補を決める（別扱いの側・ジャンルの重み・候補の幅）。
        `top` は側を決めた後のいちばん似た絵、`qualified` は使用数で選ぶ候補（`top_sim` が 0 以下なら `top` だけ）。"""
        keys, matrix, _key_set, sides, cats, pool_key = pool
        sims = matrix @ query
        raw = sims
        # 世界のジャンルの重みの分を足す（重みが全部 0 なら足さない）。似ていない外見文の下限は足す前の値で見る
        bonus = genre_bonus(pool_key, cats)
        if bonus is not None:
            sims = sims + bonus

        top_sim, top_idx = sims.max(dim=0)
        top_sim, top_idx = float(top_sim), int(top_idx)
        qualified = [top_idx]
        if top_sim > 0:
            ranked = sims.argsort(descending=True).tolist()
            side = sides[top_idx]
            if side:
                plain = next((i for i in ranked if sides[i] == ""), None)
                if float(raw[top_idx]) < SEPARATE_MIN_SIM or (
                        plain is not None and top_sim - float(sims[plain]) < SEPARATE_MARGIN):
                    side = ""
            order = [i for i in ranked if sides[i] == side][:TOP_K]
            if order:
                top_idx = order[0]
                top_sim = float(sims[order[0]])
            # 候補の幅は足す前の値で決め、比べるのは足した後の値（作者の決め。VERIFICATION.md §3.26）。
            # 比を足した後の値に掛けると、全部の絵に同じだけ足す世界で幅が 0.2 × 加算だけ広がった
            floor = top_sim - (1.0 - SIM_FLOOR) * float(raw[top_idx])
            qualified = [i for i in order if float(sims[i]) >= floor]
        return {"keys": keys, "raw": raw, "bonus": bonus, "top": top_idx, "top_sim": top_sim, "qualified": qualified}

    def best_other_slot(sub_type, query):
        """ほかの 32x32 の部位で、いちばん似た絵（足す前の値）が最も高い部位とその候補。無ければ None。"""
        best = None
        for other in CROSS_SLOTS:
            if other == sub_type:
                continue
            pool = load_pool(other)
            if pool is None:
                continue
            pick = rank(pool, query)
            if pick["top_sim"] <= 0:
                continue
            sim = float(pick["raw"][pick["top"]])
            if best is None or sim > best[0]:
                best = (sim, other, pick)
        return None if best is None else best[1:]

    def place_cross(sub_type, key):
        """部位をまたいだ絵 `xs.<元の部位>.<鍵>` の写しを、頼まれた部位のフォルダに置く。置けたら（もうあれば）True。
        元はゲームのフォルダ（カレント）か、分類の絵のフォルダ。"""
        slot, _, src_key = key[len(CROSS_PREFIX):].partition(".")
        dest = os.path.join(cross_root, IMAGE_REL, sub_type, key + ".png")
        if os.path.isfile(dest):
            return True
        rel = os.path.join(IMAGE_REL, slot, src_key + ".png")
        sources = [rel] + [os.path.join(mod_dir, "images", name, rel) for name, _setting in IMAGE_SETS]
        src = next((path for path in sources if os.path.isfile(path)), None)
        if src is None:
            log("cross: no image for {} ({})".format(key, rel))
            return False
        try:
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            shutil.copyfile(src, dest + ".tmp")
            os.replace(dest + ".tmp", dest)
        except OSError as exc:
            log("cross: cannot place {}: {!r}".format(key, exc))
            return False
        return True

    def kept_image(sub_type, query_text, key_set):
        """対応表にある絵（今も出せるものだけ）。部位をまたいだ絵は写しを確かめる。錠の中で呼ぶ。"""
        kept = state["images"].get((sub_type, query_text))
        if kept is not None and (kept in key_set or (kept.startswith(CROSS_PREFIX) and place_cross(sub_type, kept))):
            log("{}: kept {} for {!r}".format(sub_type, kept, query_text[:80]))
            return kept
        return None

    @ctx.wrap("Embedding.get_similar_id:get_similar_embedding_id", safe=True)
    def get_similar_embedding_id(orig, query_text, item_sub_type, *args, **kwargs):
        # 世界とその個別設定を先に揃える（どの分類を候補に入れるかが決まってから辞書を組む）
        with lock:
            ensure_world()
        pool = load_pool(item_sub_type)
        if pool is None:
            return orig(query_text, item_sub_type, *args, **kwargs)
        key_set = pool[2]

        # 同じ外見文には同じ絵。埋め込みの計算より先に引けるので、再入手は速い。
        with lock:
            # 表の差し替えも錠の中で。外で2本が同時に読み直すと、
            # 片方が足した対応を、もう片方が読み直した表で上書きして失う。
            ensure_world()
            kept = kept_image(item_sub_type, query_text, key_set)
            if kept is not None:
                return kept

        query = emb_mod.text_to_embedding(query_text)
        query = query.detach().reshape(-1).to(dtype=pool[1].dtype)
        query = query / query.norm().clamp_min(1e-12)
        own = rank(pool, query)
        # 部位をまたぐ候補。比べる自分の値は実際に見せる絵（使用数で選ぶ）の値なので、候補の中に
        # 0.55 未満の絵があれば先に探しておく（作者が選んだ試算と同じ比べ方。VERIFICATION.md §3.26）
        other = None
        if CROSS_SLOT and item_sub_type in CROSS_SLOTS and own["top_sim"] > 0 and min(
                float(own["raw"][i]) for i in own["qualified"]) < CROSS_SLOT_BELOW:
            other = best_other_slot(item_sub_type, query)

        # 選定と割り当ては1つの錠で。錠を分けると、同時に来た別の外見文が
        # 同じ最少の絵を取り、同じ外見文なら後の1本が先の対応を別の絵で上書きする。
        # 埋め込みの計算中に他のスレッドが同じ外見文を入れていれば、それを返す。
        with lock:
            ensure_world()
            kept = kept_image(item_sub_type, query_text, key_set)
            if kept is not None:
                return kept
            # 先に試す順（部位をまたいだ候補が決まりを満たせば先頭。写しを置けなければ自分の部位へ戻る）
            own_pick = min(own["qualified"], key=lambda i: state["usage"].get((item_sub_type, own["keys"][i]), 0))
            own_sim = float(own["raw"][own_pick])
            picks = [(item_sub_type, own)]
            if other is not None and own_sim < CROSS_SLOT_BELOW and float(other[1]["raw"][other[1]["top"]]) >= max(
                    CROSS_SLOT_MIN, own_sim + CROSS_SLOT_MARGIN):
                picks.insert(0, other)
            for slot, pick in picks:
                keys = pick["keys"]
                names = keys if slot == item_sub_type else ["{}{}.{}".format(CROSS_PREFIX, slot, k) for k in keys]
                # 近い候補がまったく無い（top_sim が 0 以下）なら qualified は top だけ ＝ 素の挙動どおり
                chosen = min(pick["qualified"],
                             key=lambda i: state["usage"].get((item_sub_type, names[i]), 0))
                key = names[chosen]
                if slot == item_sub_type or place_cross(item_sub_type, key):
                    break
            state["images"][(item_sub_type, query_text)] = key
            spot = (item_sub_type, key)
            state["usage"][spot] = state["usage"].get(spot, 0) + 1
            persist()

        raw, bonus, top_idx = pick["raw"], pick["bonus"], pick["top"]
        log("{}: chose {} (rank {} of {} qualified, sim {:.3f}{}, top1 {} {:.3f}{}) "
            "for {!r}".format(
                item_sub_type, key, pick["qualified"].index(chosen), len(pick["qualified"]),
                float(raw[chosen]),
                "" if bonus is None else " +genre {:.3f}".format(float(bonus[chosen])),
                keys[top_idx], pick["top_sim"],
                "" if slot == item_sub_type else ", from {} over own {:.3f}".format(slot, own_sim),
                query_text[:80]))
        return key

    ctx.log("item_image_variety: installed (TOP_K={}, SIM_FLOOR={}, extra dict={}, cross slot={}, image sets={})".format(
        TOP_K, SIM_FLOOR, "on" if USE_EXTRA_DICT else "off", "on" if CROSS_SLOT else "off",
        ", ".join("{} {}".format(name, mode_of(setting)) for name, setting in IMAGE_SETS)))
