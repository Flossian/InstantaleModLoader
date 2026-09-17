# -*- coding: utf-8 -*-
"""最上位エリアの地域経済プロフィールを作り、下流へ渡すMOD。

## 倍率の軸は売買画面ごとの一括商品照合

最上位エリアだけで1回、地域経済を作る。売買画面では左右の全商品を1回の一括推論へ
渡し、名前と説明文を中心に地域との関係を判定する。返った1〜5の値をプレイヤー
設定の倍率へ変換して、129が付けた買価・売価へ掛ける。

商品ごとに別々のLLM呼び出しをしない。売買画面を開く前に、全商品を1つのJSONへ
まとめて裏で検品し、完了してから元の売買画面を開く。結果は実行中のメモリだけに
保持し、商品分類をstateへ保存しない。

ゲーム側の補助分類は参考資料として渡すが、自由生成される商品に対応するため、
名前と説明文を最重視する。ゲーム側に存在する `herb` や専用画像の有無に関する
調査記録は残すが、405の特産品生成許可分類からは外す。

## 具体名の層

判定はプロフィールの特産品・過不足品と、商品の名前・説明文・補助分類をLLMへ
同時に渡す。`major_product`は関連の強さだけを示し価格は動かさず、
`unclassified`は3（等倍）にする。

## 下流エリアへの引き継ぎ

`create_settlement_detail` の `settlement_overview` が子エリアの概要として
ゲームのLLMへ渡る。世界構造から直上のエリアを特定し、そのエリアの405保存済み
プロフィールがある場合だけ、要約・スコア1の供給過多品・スコア5の需要過多品・
特産品を概要へ一時追記する。親を一段だけ見るため、孫へは直接渡さない。
ゲームの `Area` や `world_data`、セーブ、405の保存プロフィールは変更しない。

売買の左右とプレイヤー自身の所持品では、同じスコアを商品名末尾の表示へ使う。
既定の上下表示はゲーム側の名前ラベルと同じ文字表示で、商品データそのものは書き換えない。

店が本来の商品を生成した一連では、`major_products`の1品をゲーム本来の
`generate_item_in_shopping`へ1回だけ追加で渡す。312を名指しせず、初回生成と
再入荷が共通して通るゲーム側の経路だけを見る。

## 129 との層

`mod.json` の `"after"` で129の**外側**に置く。129は売買画面
（`toggle_twin_inventory_window`）でも品物欄（`ItemDetailBox.update_content`）でも
元の関数を呼ぶ**前**に値段を素から組み直すので、後段の405がその直後に掛ける。
129は同じ品を何度でも組み直すので、405も同じ2地点で掛け直す。

## スレッド

**ゲーム側のスレッドはLLMもディスクも通らない。**

    ゲーム側（move_phase / Clock）   現在地を辞書へ写して積むだけ
      → jobs キュー
        → ワーカー1本               state読み込み・LLM・state書き込み

積むかどうかは覚えている範囲（`ready` / `pending`）だけで決め、
stateファイルは読まない。読み書きは全部ワーカーの中で行う。
ワーカーは `while not ctx.superseded():` で回し、新しい注入が来たら降りる
（自前のスレッドは `revert_all()` では止まらない。TECH.md §3.6.1）。

例外は `profile_for()` の1回だけで、そこは意図して同期に読む（理由はその場に書いた）。

価格印はゲーム側のスレッドと保存の経路の両方から触るので `price_lock` で守る。
**保存後の戻し直しは控えた素の値から組み直す。現在値を軸にしない。**
軸にすると、保存の実体が走っている間に売買画面が先に掛け直していた場合、
その上へもう一度掛かって倍率が積み上がる（並行テストで 100 が 168万まで伸びた）。

## セーブ

倍率は実物の `attributes` へ書くので、保存の直前に外して保存の後に戻す。
別の街の倍率を持ち歩かないよう、エリアが変わった時点で
**素の値段へ戻してから**印を捨てる（印だけ消すと戻す手掛かりが消え、
その値段がセーブへ入る）。
"""

import json
import hashlib
import os
import queue
import sys
import threading
import typing

from instantale_modloader import frames, llm, ui
from instantale_modloader.state import (world_filename, world_key,
                                        world_key_of_dict)


# ---- 設定（mod.json の default と一致させる。tools/check_mods.py が検査する）
REGIONAL_ECONOMY_SUMMARY_CHARS = 200
REGIONAL_ECONOMY_ITEM_COUNT = 2
REGIONAL_ECONOMY_PROCESSING_STAGES = 2
# 商品照合の派生推論はMODの固定仕様とする。プレイヤー設定には公開しない。
ITEM_INFERENCE_ROUNDS = 1
STRONG_FLUCTUATION_MULTIPLIER = 1.5
WEAK_FLUCTUATION_MULTIPLIER = 1.2
# スコア表示はプレイヤー視点で固定する。設定は一括表示と売買時反転だけ。
# 1/2/4/5 は 1=↑↑、2=↑、4=↓、5=↓↓ とする。
SHOW_SCORE_MARKS = True
REVERSE_TRADE_MARK = True
SCORE_MARK_3 = True
SPECIALTY_MARK = "（特産品）"
# 安全のための内部値。プレイヤー設定には公開しない。
LLM_TIMEOUT = 120


LOG_BASENAME = "regional_economy.log"
# stateの保存先。他のMODに揃えてMODの主題の名前にする
# （npc_profiles / shop_restock / area_chronicle と同じ形）。
STATE_DIRNAME = "regional_economy"
# shared llmの自動記録をMOD名の1フォルダへ集約する。
MANAGER_NAME = "mod_regional_economy"
# プロフィール・商品一括検品・特産品生成を同じMODの監査先へまとめる。
SPECIALTY_MANAGER_NAME = MANAGER_NAME
STATE_STORE_ATTR = "__instantale_regional_economy_store__"

# ゲーム本体の町詳細生成。`settlement_overview` が生成対象エリアの
# 概要としてプロンプトへ渡るため、親エリアの経済情報はここへだけ足す。
SETTLEMENT_DETAIL_TARGET = (
    "scripts.llm.llm_manager_world_generate:create_settlement_detail"
)
ECONOMY_CONTEXT_HEADER = "【この土地の産業と経済】"
STRUCTURE_NAME_KEYS = ("settlement_name", "area_name", "name")
STRUCTURE_ID_KEYS = ("area_id", "id")
WORLD_OVERVIEW_CHARS = 2400
AREA_OVERVIEW_CHARS = 2400
LIST_ITEM_CHARS = 240
ITEM_DESCRIPTION_CHARS = 1200
# 売買の窓だけに掛ける。所持品は None、402の受け渡しは "party_transfer"
# （GAME.md §2.13）。地域の需給は交易の値段の話なので、
# 売り買いをしない窓では触らない。
TRADE_SITUATION = "shop"
BUY_KEY = "買価"
SELL_KEY = "売価"
PRICE_KEYS = (BUY_KEY, SELL_KEY)
FIXED_SCORE_MARKS = {1: "↑↑", 2: "↑", 4: "↓", 5: "↓↓"}
SPECIALTY_MARK_CHOICES = ("（特産品）", "※", "★", "なし")
VALID_RARITIES = {
    "common", "rare", "magical", "epic", "legendary", "mythic",
}
# ゲームの構造化出力で許されている item_category / sub_type。
# 画像の選択はゲーム自身の generate_item_in_shopping に任せ、405側で
# 代替画像を決めない。`herb`は現在の許可sub_typeには含めない。
VALID_SUBTYPES = {
    "weapon": {"small", "medium", "large", "long", "throwable"},
    "wearable": {
        "headgear", "body_armor", "legwear", "gauntlets", "shield",
        "accessory", "clothing",
    },
    "consumable": {
        "food", "drink", "medicine", "potion", "scroll",
        "plant", "mushroom",
    },
    "healing_item": {"food", "drink", "medicine", "potion", "plant"},
    "material": {
        "creature_part", "creature", "ore", "metal", "gem", "treasure",
        "plant", "mushroom", "relic", "scrap", "magical_material",
        "other_material", "liquid_material",
    },
    "utility": {"tool", "document", "scroll"},
}

# 品の細分（`attributes["item_detail"]`）と、LLMへ渡す訳語。
# `herb`は現在の特産品生成では使わないため405の許可分類から外している。
# ゲーム側に存在すること、専用画像の有無に関する調査記録はDOC.mdへ残す。
# ここに無い細分が来ても落ちない（スコア3＝等倍として扱う）。
GENRES = {
    "small_weapon": "短剣・小型の武器",
    "medium_weapon": "片手剣などの中型の武器",
    "large_weapon": "大剣などの大型の武器",
    "long_weapon": "槍・長物",
    "throwable_weapon": "投擲武器",
    "headgear": "兜・頭の防具",
    "body_armor": "鎧・胴の防具",
    "legwear": "脚の防具",
    "gauntlets": "手甲",
    "shield": "盾",
    "accessory": "装身具・装飾品",
    "clothing": "衣服",
    "food": "食料",
    "drink": "飲み物",
    "plant": "薬草・植物",
    "mushroom": "きのこ",
    "medicine": "薬",
    "potion": "調合された薬品",
    "scroll": "巻物",
    "tool": "道具",
    "document": "書物・文書",
    "creature": "生き物",
    "creature_part": "生き物の部位",
    "ore": "鉱石",
    "metal": "金属・インゴット",
    "gem": "宝石",
    "treasure": "財宝",
    "relic": "遺物",
    "scrap": "がらくた",
    "magical_material": "魔法の素材",
    "liquid_material": "液体の素材",
    "other_material": "その他の素材",
}

# LLMが返したジャンルの数がこれを下回るプロフィールは保存しない。
# 半端な表は「鉱石だけ安くて他は全部平常」のような歪んだ経済になる。
# 保存しなければ次に着いたときへ持ち越される。
MIN_GENRE_SCORES = 8

_FULLWIDTH_DIGITS = str.maketrans("１２３４５", "12345")


def _get(value, name, default=None):
    """属性と辞書の両方から値を読む。"""
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def _active_world_context(app):
    """現在遊んでいる世界の鍵と、同じ世界のデータ源を返す。

    通常は `app.world_dict`（世界ファイル）と
    `app.save_data_dict`（現在のプレイ中セーブ）の `world_data` が同じ名前に
    なる。ところがロード直後や世界を切り替えた直後は、実行時の
    `world_dict` が前の世界を指したまま、セーブ側だけが新しい世界を指す
    瞬間がある。このとき共有部品の `world_key(app)` だけに任せると、
    同じエリアIDを持つ別世界のプロフィールを拾う可能性がある。

    そこで、両方を共有部品の `world_key_of_dict` で照合する。名前が一致する
    ときは世界ファイルを使い、食い違うときは「いまプレイヤーが遊んでいる
    セーブ」の名前とデータを採用する。405はゲーム本体やセーブを書き換えず、
    参照元だけを選ぶ。
    """
    if app is None:
        return "", None, ""

    world_dict = getattr(app, "world_dict", None)
    save_dict = getattr(app, "save_data_dict", None)
    world_name = world_key_of_dict(world_dict, None)
    save_name = world_key_of_dict(save_dict, None)

    if isinstance(world_name, str) and world_name:
        if isinstance(save_name, str) and save_name and save_name != world_name:
            return save_name, save_dict, "save_data_dict"
        return world_name, world_dict, "world_dict"
    if isinstance(save_name, str) and save_name:
        return save_name, save_dict, "save_data_dict"

    fallback = world_key(app)
    if isinstance(fallback, str) and fallback:
        return fallback, None, "runtime"
    return "", None, ""


def _active_world_key(app):
    """現在の世界名だけを取得する。stateの鍵は必ずこれを使う。"""
    return _active_world_context(app)[0]


def _active_world_data(app):
    """現在の世界に対応する辞書を取得する。"""
    return _active_world_context(app)[1]


def _active_world_structure(app):
    """現在の世界の構造を取得する。"""
    container = _active_world_data(app)
    if isinstance(container, dict):
        world_data = container.get("world_data")
        structure = _get(world_data, "structure")
        if structure is not None:
            return structure
    return _get(getattr(app, "world", None), "structure")


def _short(value, limit):
    """共有部品の文字数制限へ通す。"""
    if value is None:
        return ""
    if not isinstance(value, str):
        value = str(value)
    value = value.strip()
    return frames.short(value, limit) if value else ""


def _overview_only(value, limit):
    """overviewだけを取り出し、細かなロケーションを混ぜない。"""
    if isinstance(value, dict):
        value = value.get("overview")
    elif isinstance(value, (list, tuple)):
        value = next((item for item in value if isinstance(item, str)), "")
    return _short(value, limit)


def _world_overview(app):
    """world_data.overviewだけを読む。"""
    world_dict = _active_world_data(app)
    if isinstance(world_dict, dict):
        world_data = world_dict.get("world_data")
        if isinstance(world_data, dict):
            text = _overview_only(world_data.get("overview"),
                                  WORLD_OVERVIEW_CHARS)
            if text:
                return text
    return _overview_only(_get(getattr(app, "world", None), "overview"),
                          WORLD_OVERVIEW_CHARS)


def _area_overview(area):
    """Area.descriptions['overview']だけを読む。"""
    descriptions = _get(area, "descriptions")
    text = _overview_only(descriptions, AREA_OVERVIEW_CHARS)
    if text:
        return text
    return _overview_only(_get(area, "overview"), AREA_OVERVIEW_CHARS)


def _structure_identity(node):
    """世界構造の1ノードから、保存照合用の `(id, name)` を読む。"""
    if isinstance(node, str):
        return "", node.strip()
    if not isinstance(node, dict):
        return "", ""
    node_id = next((node.get(key) for key in STRUCTURE_ID_KEYS
                    if node.get(key) not in (None, "")), "")
    node_name = next((node.get(key) for key in STRUCTURE_NAME_KEYS
                      if node.get(key) not in (None, "")), "")
    return (str(node_id) if node_id not in (None, "") else "",
            node_name.strip() if isinstance(node_name, str) else "")


def _structure_parent(structure, target_name, target_id=None):
    """対象ノードの直上だけを `(親ID, 親名)` として返す。"""
    wanted_name = target_name.strip() if isinstance(target_name, str) else ""
    wanted_id = str(target_id) if target_id not in (None, "") else ""
    if not wanted_name and not wanted_id:
        return None
    visited = set()

    def visit(node, parent):
        if isinstance(node, (dict, list, tuple)):
            marker = id(node)
            if marker in visited:
                return None
            visited.add(marker)
        node_id, node_name = _structure_identity(node)
        matches = (wanted_id and node_id == wanted_id) if node_id else \
            (wanted_name and node_name == wanted_name)
        if matches:
            return parent
        next_parent = (node_id, node_name) if (node_id or node_name) else parent
        if isinstance(node, dict):
            for key, value in node.items():
                if key in STRUCTURE_ID_KEYS + STRUCTURE_NAME_KEYS:
                    continue
                found = visit(value, next_parent)
                if found is not None:
                    return found
        elif isinstance(node, (list, tuple)):
            for value in node:
                found = visit(value, parent)
                if found is not None:
                    return found
        return None

    return visit(structure, None)


def _economy_context(record):
    """親エリアから子エリアへ渡す短い経済ブロックを作る。"""
    if not isinstance(record, dict):
        return ""
    summary = _short(record.get("regional_economy_summary"),
                     REGIONAL_ECONOMY_SUMMARY_CHARS)
    if not summary:
        return ""

    def goods(key):
        values = record.get(key)
        if isinstance(values, str):
            values = [values]
        if not isinstance(values, (list, tuple)):
            return ""
        result = [_clean_item_text(value) for value in values]
        return "、".join(value for value in result if value)

    lines = [ECONOMY_CONTEXT_HEADER, summary]
    surplus = goods("surplus_goods")
    shortage = goods("shortage_goods")
    products = goods("major_products")
    if surplus:
        lines.append("供給過多の製品: " + surplus)
    if shortage:
        lines.append("需要過多の製品: " + shortage)
    if products:
        lines.append("特産品: " + products)
    return "\n".join(lines)


def _area_id_by_name(app, area_name):
    """構造側にIDが無い版だけ、実行中のArea一覧で名前をIDへ戻す。"""
    if app is None or not isinstance(area_name, str) or not area_name.strip():
        return ""
    for area_id, area in (ui.world_areas(app) or {}).items():
        if _short(_get(area, "name", ""), 120).strip() == area_name.strip():
            return str(area_id)
    return ""


def _snapshot(app):
    """メインスレッドで採る、地域経済生成用の最小資料。"""
    if app is None:
        return None
    area = ui.current_area(app)
    area_id = ui.area_id_of(area)
    if area is None or not area_id:
        return None
    area_name = _short(_get(area, "name", ""), 120) or area_id
    return {
        "world_key": _short(_active_world_key(app), 240),
        "area_id": str(area_id),
        "area_name": area_name,
        "world_overview": _world_overview(app),
        "area_overview": _area_overview(area),
    }


def _scope_of(app):
    """いまの取引地点 `(世界, エリアid)`。読めなければ None。

    売買画面と品物欄で**同じ鍵**にする。`_snapshot()` が組む値と
    1文字も違えてはいけない（違うと品物欄がプロフィールを引けず、
    129が戻した素の値段のまま出る）。
    """
    if app is None:
        return None
    area = ui.current_area(app)
    area_id = str(ui.area_id_of(area) or "")
    if area is None or not area_id:
        return None
    return (str(_short(_active_world_key(app), 240) or "_"), area_id)


def _new_bucket(world):
    return {
        "world_key": world,
        "areas": {},
    }


def _store():
    """再注入を跨いでプロフィール用ワーカーを共有する。"""
    found = getattr(sys, STATE_STORE_ATTR, None)
    if not isinstance(found, dict):
        found = {}
        setattr(sys, STATE_STORE_ATTR, found)
    defaults = {
        "buckets": {},
        "blocked_worlds": set(),
        "pending": set(),
        # プロフィール待ちが必要なのは、ゲームが初回の店の商品を作り始めたのに
        # 到着時の裏仕事がまだ終わっていない1回だけ。Eventはプロセス内限定。
        "profile_events": {},
        # 生成済みと分かっている取引地点。ゲーム側のスレッドが
        # stateファイルを読まずに「積むか」を決めるための控え。
        "ready": set(),
        "skip_logged": set(),
        "jobs": queue.Queue(),
        "worker": None,
        "data_lock": threading.RLock(),
        "worker_lock": threading.Lock(),
        # ゲームの1回の品揃え生成につき、特産品生成を1度だけ確保する。
        "specialty_lock": threading.RLock(),
        "stock_batch": None,
        # 価格印はゲーム側のスレッド（売買画面・品物欄）と
        # 保存の経路の両方から触る。辞書が途中の形で読まれないようにする。
        "price_lock": threading.RLock(),
        "price_marks": {},
        # 商品分類は永続化しない。売買画面の一括検品が完了した後、
        # 同じ商品内容を再利用するための実行中だけの控え。
        "classification_lock": threading.RLock(),
        "classifications": {},
        "classification_pending": set(),
        # 保存中の印。`save_game` が内側で保存の実体を呼ぶので、
        # 二重に戻さないための門番。読んで書くまでを割り込ませない。
        "save_lock": threading.Lock(),
        "save_in_progress": False,
        # world_dict と現在セーブの名前が一時的に食い違ったことを、
        # 同じロード中に何度も書かないための控え。
        "world_identity_mismatches": set(),
    }
    for key, value in defaults.items():
        if key not in found:
            found[key] = value
    if not isinstance(found.get("price_marks"), dict):
        found["price_marks"] = {}
    if not isinstance(found.get("profile_events"), dict):
        found["profile_events"] = {}
    if not isinstance(found.get("classifications"), dict):
        found["classifications"] = {}
    if not isinstance(found.get("classification_pending"), set):
        found["classification_pending"] = set()
    if not isinstance(found.get("world_identity_mismatches"), set):
        found["world_identity_mismatches"] = set()
    return found


def _state_path(ctx, world):
    return ctx.state_path(STATE_DIRNAME, world_filename(world))


def _load_bucket(ctx, state, world, write):
    """既存stateが壊れていても空として上書きしない。"""
    with state["data_lock"]:
        if world in state["blocked_worlds"]:
            return None
        cached = state["buckets"].get(world)
        if cached is not None:
            return cached

        path = _state_path(ctx, world)
        existed = os.path.isfile(path)
        data = ctx.read_json(path, None)
        if data is None:
            if existed:
                state["blocked_worlds"].add(world)
                write("state unreadable; refusing to recreate {!r}".format(world))
                return None
            bucket = _new_bucket(world)
        elif not isinstance(data, dict):
            state["blocked_worlds"].add(world)
            write("state is not an object; refusing to overwrite {!r}".format(world))
            return None
        elif data.get("world_key") != world:
            state["blocked_worlds"].add(world)
            write("state world mismatch for {!r}; leaving it untouched".format(world))
            return None
        elif not isinstance(data.get("areas"), dict):
            state["blocked_worlds"].add(world)
            write("state areas are invalid for {!r}; leaving it untouched".format(world))
            return None
        else:
            bucket = data
        state["buckets"][world] = bucket
        return bucket


def _record_of(bucket, area_id):
    areas = bucket.get("areas") if isinstance(bucket, dict) else None
    return areas.get(area_id) if isinstance(areas, dict) else None


def _record_ready(record):
    """倍率まで揃ったプロフィールか。

    ジャンル表を持たない控え（品ごとにLLMへ聞いていた頃の版）は
    「無い」ものとして扱い、作り直させる。
    """
    if not isinstance(record, dict):
        return False
    scores = record.get("genre_scores")
    return isinstance(scores, dict) and len(scores) >= MIN_GENRE_SCORES


def _clean_item_text(value):
    """LLMの配列要素から箇条書き記号だけを取り除く。"""
    if isinstance(value, dict):
        for key in ("name", "item", "goods", "product", "text"):
            if isinstance(value.get(key), str):
                value = value[key]
                break
        else:
            return ""
    if not isinstance(value, str):
        return ""
    text = value.strip()
    while text.startswith(("-", "・", "•", "*")):
        text = text[1:].strip()
    return _short(text, LIST_ITEM_CHARS)


def _list_value(value):
    if isinstance(value, str):
        values = [value]
    elif isinstance(value, (list, tuple)):
        values = list(value)
    else:
        return None
    result = []
    seen = set()
    for value in values:
        text = _clean_item_text(value)
        if text and text not in seen:
            seen.add(text)
            result.append(text)
        if len(result) >= REGIONAL_ECONOMY_ITEM_COUNT:
            break
    if len(result) != REGIONAL_ECONOMY_ITEM_COUNT:
        return None
    return result


def _score_value(value):
    """1〜5の整数として読む。読めなければ None。"""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        value = str(value)
    elif isinstance(value, float):
        value = str(int(round(value)))
    elif isinstance(value, str):
        value = value.strip().translate(_FULLWIDTH_DIGITS)
    else:
        return None
    return int(value) if value in ("1", "2", "3", "4", "5") else None


def _genre_scores(value):
    """LLMの返しを `{ジャンル: 1〜5}` へ均す。知らない綴りは捨てる。

    配列（`[{"genre":..., "score":...}]`）でも辞書でも受ける。
    必須フィールドを32個並べたスキーマはローカルモデルが守らないので、
    要求するのは配列にしてある。取りこぼしたジャンルは3（等倍）扱い。
    """
    if isinstance(value, dict):
        pairs = list(value.items())
    elif isinstance(value, (list, tuple)):
        pairs = []
        for entry in value:
            if not isinstance(entry, dict):
                continue
            name = None
            for key in ("genre", "item_detail", "name", "category"):
                if isinstance(entry.get(key), str):
                    name = entry[key]
                    break
            pairs.append((name, entry.get("score")))
    else:
        return None
    result = {}
    for name, score in pairs:
        if not isinstance(name, str):
            continue
        name = name.strip().casefold()
        if name not in GENRES:
            continue
        number = _score_value(score)
        if number is not None:
            result[name] = number
    return result or None


def _normalize_profile(data):
    """地域経済プロフィールを、保存できる形へ固定する。"""
    if not isinstance(data, dict):
        return None
    profile = {
        "regional_economy_summary": _short(
            data.get("regional_economy_summary"),
            REGIONAL_ECONOMY_SUMMARY_CHARS),
        "major_industries": _list_value(data.get("major_industries")),
        "major_products": _list_value(data.get("major_products")),
        "surplus_goods": _list_value(data.get("surplus_goods")),
        "shortage_goods": _list_value(data.get("shortage_goods")),
        "genre_scores": _genre_scores(data.get("genre_scores")),
    }
    if not profile["regional_economy_summary"]:
        return None
    if any(profile[key] is None for key in (
            "major_industries", "major_products", "surplus_goods",
            "shortage_goods", "genre_scores")):
        return None
    if len(profile["genre_scores"]) < MIN_GENRE_SCORES:
        return None
    if set(profile["surplus_goods"]) & set(profile["shortage_goods"]):
        return None
    return profile


def _raw_dict(raw):
    """構造化出力とJSON文字列を辞書へ揃える。"""
    data = llm.as_dict(raw)
    if isinstance(data, dict):
        return data
    if not isinstance(raw, str):
        return None
    body = raw.strip()
    start, end = body.find("{"), body.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(body[start:end + 1])
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _item_snapshot(item):
    """商品オブジェクトを、LLMへ渡す短い辞書へ写す。"""
    if item is None:
        return None
    attributes = _get(item, "attributes", {})
    if not isinstance(attributes, dict):
        attributes = {}
    name = _short(_get(item, "name", ""), 240)
    description = _short(_get(item, "description", ""), ITEM_DESCRIPTION_CHARS)
    item_type = _short(_get(item, "item_type", ""), 120)
    item_detail = ""
    for key in ("item_detail", "category", "subtype", "material"):
        item_detail = _short(attributes.get(key), 240)
        if item_detail:
            break
    rarity = _short(_get(item, "rarity", ""), 80)
    item_id = _short(_get(item, "id", ""), 160)
    if not name and not description:
        return None
    identity = {
        "id": item_id,
        "name": name,
        "description": description,
        "item_type": item_type,
        "item_detail": item_detail,
        "rarity": rarity,
    }
    digest = hashlib.sha256(json.dumps(
        identity, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()[:24]
    item_key = ("id:{}:{}".format(item_id, digest) if item_id
                else "content:{}".format(digest))
    return {
        "item_key": item_key,
        "item_id": item_id,
        "name": name,
        "description": description,
        "item_type": item_type,
        "item_detail": item_detail,
        "rarity": rarity,
    }


def _item_snapshots(*obtainers):
    """左右の全商品を入力順で取り出す。出力へは実体を含めない。"""
    result = []
    counts = {}
    for obtainer in obtainers:
        for item in _inventory_items(obtainer):
            snapshot = _item_snapshot(item)
            if snapshot is None:
                continue
            base_key = snapshot["item_key"]
            counts[base_key] = counts.get(base_key, 0) + 1
            occurrence = counts[base_key]
            if occurrence > 1:
                snapshot["item_key"] = "{}#{}".format(base_key, occurrence)
            snapshot["_runtime_item"] = item
            result.append(snapshot)
    return result


def _classification_name(value):
    """LLMの返しを、分類名として安全な文字列へ揃える。"""
    if not isinstance(value, str):
        return "unclassified"
    value = value.strip().casefold()
    if value in ("surplus_good", "shortage_good", "major_product",
                 "unclassified"):
        return value
    return "unclassified"


def _relation_name(value):
    """LLMの返しを、関係名として安全な文字列へ揃える。"""
    if not isinstance(value, str):
        return "none"
    value = value.strip().casefold()
    return value if value in ("direct", "derived", "none") else "none"


def _matched_name(value):
    """LLMの返しを、物品名として安全な文字列へ揃える。"""
    return _clean_item_text(value)


def _source_buckets(record):
    """地域プロフィールの物品名を、方向判定用の一時索引へ展開する。"""
    result = {}
    for field, bucket_name in (
            ("major_products", "major_product"),
            ("surplus_goods", "surplus_good"),
            ("shortage_goods", "shortage_good")):
        values = record.get(field) if isinstance(record, dict) else None
        if not isinstance(values, (list, tuple)):
            continue
        for value in values:
            name = _clean_item_text(value)
            if name:
                result.setdefault(_name_key(name), set()).add(bucket_name)
    return result


def _default_classification(item, reason):
    """LLMの返しが無い場合の安全な1分類を作る。"""
    return {
        "item_key": item["item_key"],
        "item_name": _short(item.get("name", ""), 240),
        "classification": "unclassified",
        "score": 3,
        "matched_goods": [],
        "relation": "none",
        "reason": reason,
    }


def _normalize_classification(data, item, record):
    """LLMの返しを、保存できる形へ固定する。"""
    if not isinstance(data, dict):
        return _default_classification(item, "一括結果にこの商品が無かったため未分類。")
    classification = _classification_name(data.get("classification"))
    relation = _relation_name(data.get("relation"))
    raw_matches = data.get("matched_goods", [])
    if isinstance(raw_matches, str):
        raw_matches = [raw_matches]
    if not isinstance(raw_matches, (list, tuple)):
        raw_matches = []
    matched = []
    for value in raw_matches:
        name = _matched_name(value)
        if name and name not in matched:
            matched.append(name)
        if len(matched) >= REGIONAL_ECONOMY_ITEM_COUNT:
            break
    score = _score_value(data.get("score"))
    if classification == "unclassified" or score is None:
        classification = "unclassified"
        relation = "none"
        score = 3
    else:
        source = _source_buckets(record)
        directions = set()
        for name in matched:
            directions.update(source.get(_name_key(name), set()))
        directions.discard("major_product")
        if len(directions) > 1:
            classification = "unclassified"
            relation = "none"
            score = 3
        elif directions:
            classification = next(iter(directions))
        if classification == "surplus_good" and score > 3:
            classification = "unclassified"
            relation = "none"
            score = 3
        elif classification == "shortage_good" and score < 3:
            classification = "unclassified"
            relation = "none"
            score = 3
        elif not matched:
            classification = "unclassified"
            relation = "none"
            score = 3
    return {
        "item_key": item["item_key"],
        "item_name": _short(item.get("name", ""), 240),
        "classification": classification,
        "score": score,
        "matched_goods": matched,
        "relation": relation,
        "reason": _short(data.get("reason", ""), 240),
    }


def _build_messages(snapshot):
    """systemへ指示、userへ現在エリアの最小資料を分けて渡す。"""
    json_format = json.dumps({
        "regional_economy_summary": "要約",
        "major_industries": [
            "主要産業{}".format(number)
            for number in range(1, REGIONAL_ECONOMY_ITEM_COUNT + 1)
        ],
        "major_products": [
            "特産品{}".format(number)
            for number in range(1, REGIONAL_ECONOMY_ITEM_COUNT + 1)
        ],
        "surplus_goods": [
            "供給過多品{}".format(number)
            for number in range(1, REGIONAL_ECONOMY_ITEM_COUNT + 1)
        ],
        "shortage_goods": [
            "不足品{}".format(number)
            for number in range(1, REGIONAL_ECONOMY_ITEM_COUNT + 1)
        ],
        "genre_scores": [
            {"genre": "ore", "score": 2},
            {"genre": "potion", "score": 4},
        ],
    }, ensure_ascii=False)
    genre_lines = "\n".join(
        "・{} : {}".format(name, gloss) for name, gloss in GENRES.items())
    system_content = (
        "あなたは下記の世界観を持つ架空世界での経済を考え、作成する担当です。"
        "以下に従い、JSONオブジェクト1個だけを返してください。\n\n"
        "【経済の前提】\n"
        "・世界には幾つかの都市や街、集落が存在します（エリア）。\n"
        "・各エリアで様々な物品が採取、採掘、加工、生産が行われています。\n"
        "・各エリアでは交易が行われています。\n"
        "・制作するのは1つのエリアであり、世界全体ではありません。必ず得手不得手が存在します。\n\n"
        "【地域経済の制作】\n"
        "- regional_economy_summary:このエリアだけで行われている経済活動を概要として日本語で{}文字程度記述。\n"
        "- major_industries:このエリアだけでの主要産業を{}つ日本語で記述。\n"
        "- major_products:このエリアでの特産品を{}つ日本語で記述。\n"
        "- surplus_goods:このエリアで供給過多になりやすい物品を{}つ日本語で記述。"
        "（注意：特産品と同じでも構わないが、供給過多になりやすいのに何故特産品であるのかは妥当性を考えること）\n"
        "- shortage_goods:このエリアで不足・輸入依存である物品を{}つ日本語で記述\n\n"
        "【規則】\n"
        "・『金属』『食料』のように一般化させてはならない。"
        "（例：現実世界でいえば（金属→銅鉱石、食料→トウモロコシ）のように、細分化すること。"
        "これは現実世界での例であり、必ず世界観に則って考える）\n"
        "・素材が含まれるだけで特産品とは断定せず、採掘・精錬・加工のどの段階かを区別する。"
        "（例：現実世界でいえば（石油→ガソリンは2段階、銅鉱石→銅のインゴット→銅の鍋は3段階、"
        "羊→羊毛→羊毛布→羊毛布団は4段階）といった具合で考える。世界観に従い、"
        "謎の産出物は{}段階を経て、製品になると考える）\n"
        "・major_products、surplus_goods、shortage_goodsの物品名は必ず日本語で書く。\n"
        "・上記3欄の物品名へ『高級な』『上質な』などの品質・価値を飾る語を絶対に付けない。"
        "物品そのものを指す、短く具体的な普通名詞にする。\n"
        "・上記3欄では『魔物の』『竜の』『○○由来の』など、所有・出所を前置きする接続語を原則使わない。"
        "世界観上どうしても物品を識別できない場合だけ使い、同じ接続語は3欄を通して1度までとする。\n"
        "・surplus_goodsとshortage_goodsに同じ物品は記述してはならない。\n\n"
        "【ジャンル別の需給】\n"
        "- genre_scores:下記の全ジャンルについて、このエリアでの需給を"
        "1〜5の整数で付ける。配列の要素は {{\"genre\": 綴り, \"score\": 整数}} とし、"
        "genre には下記の綴りをそのまま使う（訳語や日本語を入れない）。\n"
        "・3が平常。1へ寄るほど供給過多で安く、5へ寄るほど不足・需要過多で高い。\n"
        "・上で書いた主要産業・特産品・供給過多品・不足品と矛盾させない。"
        "産地のジャンルは1か2、輸入に頼るジャンルは4か5になるはずである。\n"
        "・**全てを3にしてはならない。** 必ず得手不得手があるので、"
        "少なくとも幾つかは1〜2へ、幾つかは4〜5へ振り分ける。\n"
        "・そのエリアと縁の薄いジャンルは3でよい。\n\n"
        "【ジャンルの綴りと意味】\n{}\n\n"
    ).format(
        REGIONAL_ECONOMY_SUMMARY_CHARS,
        REGIONAL_ECONOMY_ITEM_COUNT,
        REGIONAL_ECONOMY_ITEM_COUNT,
        REGIONAL_ECONOMY_ITEM_COUNT,
        REGIONAL_ECONOMY_ITEM_COUNT,
        REGIONAL_ECONOMY_PROCESSING_STAGES,
        genre_lines,
    ) + "【JSON形式】\n" + json_format
    user_content = json.dumps(snapshot, ensure_ascii=False, indent=2)
    return [
        {"role": "system", "content": system_content},
        {"role": "user", "content": user_content},
    ]


def _ask_profile(ctx, write, snapshot):
    score_structure = llm.create_structure(
        ctx,
        "RegionalEconomyGenreScore",
        {"genre": (str, ...), "score": (int, ...)},
        label="regional economy genre",
    )
    score_type = typing.List[typing.Dict[str, str]]
    if score_structure is not None:
        try:
            score_type = typing.List[score_structure]
        except Exception:
            score_type = typing.List[typing.Dict[str, str]]
    structure = llm.create_structure(
        ctx,
        "RegionalEconomySummary",
        {
            "regional_economy_summary": (str, ...),
            "major_industries": (typing.List[str], ...),
            "major_products": (typing.List[str], ...),
            "surplus_goods": (typing.List[str], ...),
            "shortage_goods": (typing.List[str], ...),
            "genre_scores": (score_type, ...),
        },
        label="regional economy",
    )
    raw = llm.ask(
        ctx,
        MANAGER_NAME,
        _build_messages(snapshot),
        timeout=LLM_TIMEOUT,
        structure=structure,
        max_tokens=((REGIONAL_ECONOMY_SUMMARY_CHARS +
                     REGIONAL_ECONOMY_ITEM_COUNT * LIST_ITEM_CHARS) * 4 +
                    len(GENRES) * 40),
        label="regional economy profile",
        write=write,
    )
    return _normalize_profile(_raw_dict(raw))


def _build_classification_messages(snapshot, record, items):
    """systemへ商品一式、userへ地域経済要約を置く一括検品メッセージ。"""
    rounds = max(0, min(2, int(ITEM_INFERENCE_ROUNDS)))
    instruction = (
        "あなたは、ゲーム上の架空都市における地域経済を商品に反映させる役割です。"
        "JSONオブジェクト1個だけを返してください。\n\n"
        "【指示】\n"
        "両者の持つ全てのアイテムに、1～5の整数刻みで数値を割り当てる。\n"
        "・別の都市や世界の経済を混ぜてはならない。\n"
        "・商品の名前と説明文を最重視し、item_type等は補助資料として使う。\n"
        "・名前が地域経済欄と一字一句同じでなくても、意味として同じ物品かを考える。\n"
        "・素材が含まれるだけで、このエリアが産地・特産品とは断定しない。商品が産業工程のどこに位置するかを考える。"
        "（例：銅鉱石が産出しても、銅製品の名産地とは限らない）\n"
        "・このエリアで生産される物品や不足している物品から、<{}>回派生を推論してよい。"
        "（1回の推論例：「電池→懐中電灯」（懐中電灯には電池も含まれるであろう））"
        "<{}>回を超える推論は行わないこと。\n"
        "・1つの物品に1つの数値のみ割り当てる。複数の数値が1つの物品に存在する場合は、"
        "3から最も離れた1つの数値を選ぶ。供給過多と不足が同時に候補になり判断できない場合は3にする。\n"
        "・正当な関係が無い場合は未分類とし、数値は3にする。\n\n"
        "【出力規則】\n"
        "- classification は surplus_good、shortage_good、major_product、unclassified のいずれかを記述。"
        "（surplus_good は供給過多側、shortage_good は不足・需要過多側、major_product は特産品だが過不足欄の方向を採用しない場合、unclassified は補正不要。）\n"
        "- score:1〜5の整数数値。3が等倍、1側が供給過多、5側が不足・需要過多。"
        "major_productの場合も地域との関連の強さを1〜5で示す。\n"
        "- matched_goods は地域経済欄から照合に使った物品名を記載する。該当しなければ空配列。\n"
        "- relation:direct、derived、none のいずれか。\n"
        "- reason:判断理由を日本語で短く記載。推論を行った場合はA→B→C...といった、推論の経由も記載。\n\n"
    ).format(rounds, rounds)
    location = json.dumps({
        "world_key": snapshot.get("world_key", ""),
        "area_id": snapshot.get("area_id", ""),
        "area_name": snapshot.get("area_name", ""),
    }, ensure_ascii=False, indent=2)
    item_data = []
    for item in items:
        item_data.append({
            "item_key": item.get("item_key", ""),
            "name": item.get("name", ""),
            "description": item.get("description", ""),
            "item_type": item.get("item_type", ""),
            "item_detail": item.get("item_detail", ""),
            "rarity": item.get("rarity", ""),
            "item_id": item.get("item_id", ""),
        })
    system_content = (
        instruction + "【取引地点】\n" + location +
        "\n\n【売買画面の全商品】\n" +
        json.dumps(item_data, ensure_ascii=False, indent=2)
    )
    user_content = json.dumps({
        "regional_economy_summary": {
            "summary": record.get("regional_economy_summary", ""),
            "major_industries": record.get("major_industries", []),
            "major_products": record.get("major_products", []),
            "surplus_goods": record.get("surplus_goods", []),
            "shortage_goods": record.get("shortage_goods", []),
        },
    }, ensure_ascii=False, indent=2)
    return [
        {"role": "system", "content": system_content},
        {"role": "user", "content": user_content},
    ]


def _normalize_batch(data, items, record):
    """一括JSONを入力順・全商品1件ずつの結果へ揃える。"""
    raw_items = data.get("items", []) if isinstance(data, dict) else []
    if not isinstance(raw_items, (list, tuple)):
        raw_items = []
    by_key = {}
    by_name = {}
    for raw in raw_items:
        if not isinstance(raw, dict):
            continue
        key = raw.get("item_key")
        if isinstance(key, str) and key and key not in by_key:
            by_key[key] = raw
        name = raw.get("item_name")
        if isinstance(name, str):
            by_name.setdefault(_name_key(name), []).append(raw)
    used = set()
    result = []
    for item in items:
        raw = by_key.get(item.get("item_key"))
        if raw is None:
            candidates = by_name.get(_name_key(item.get("name", "")), [])
            raw = next((candidate for candidate in candidates
                        if id(candidate) not in used), None)
        if raw is None:
            result.append(_default_classification(
                item, "一括結果にこの商品が無かったため未分類。"))
            continue
        used.add(id(raw))
        result.append(_normalize_classification(raw, item, record))
    return result


def _ask_classification(ctx, write, snapshot, record, items):
    """左右の全商品を1回の構造化LLM呼び出しへ渡す。"""
    item_fields = {
        "item_key": (str, ...),
        "classification": (str, ...),
        "score": (int, ...),
        "matched_goods": (typing.List[str], ...),
        "relation": (str, ...),
        "reason": (str, ...),
    }
    item_structure = llm.create_structure(
        ctx, "RegionalEconomyItemClassification", item_fields,
        label="regional economy item",
    )
    item_type = typing.List[typing.Dict[str, str]]
    if item_structure is not None:
        try:
            item_type = typing.List[item_structure]
        except Exception:
            item_type = typing.List[typing.Dict[str, str]]
    structure = llm.create_structure(
        ctx,
        "RegionalEconomyItemClassificationBatch",
        {"items": (item_type, ...)},
        label="regional economy item batch",
    )
    raw = llm.ask(
        ctx,
        MANAGER_NAME,
        _build_classification_messages(snapshot, record, items),
        timeout=LLM_TIMEOUT,
        structure=structure,
        max_tokens=max(2400, len(items) * 240),
        label="regional economy item batch",
        write=write,
    )
    data = _raw_dict(raw)
    if not isinstance(data, dict):
        return None
    return _normalize_batch(data, items, record)


def _save_profile(ctx, state, write, snapshot, profile):
    """プロフィールの無いエリアと、古い形の控えだけを書く。"""
    world = snapshot["world_key"]
    area_id = snapshot["area_id"]
    with state["data_lock"]:
        bucket = _load_bucket(ctx, state, world, write)
        if bucket is None:
            return False
        areas = bucket.get("areas", {})
        if _record_ready(areas.get(area_id)):
            return False
        record = {
            "area_name": snapshot["area_name"],
            "regional_economy_summary": profile["regional_economy_summary"],
            "major_industries": profile["major_industries"],
            "major_products": profile["major_products"],
            "surplus_goods": profile["surplus_goods"],
            "shortage_goods": profile["shortage_goods"],
            "genre_scores": profile["genre_scores"],
        }
        updated = {
            "world_key": world,
            "areas": dict(areas),
        }
        updated["areas"][area_id] = record
        if not ctx.write_json(_state_path(ctx, world), updated, indent=1):
            write("could not save summary for {!r} / {!r}".format(
                world, area_id))
            return False
        state["buckets"][world] = updated
        moved = sorted(
            (name for name, score in profile["genre_scores"].items()
             if score != 3),
            key=lambda name: profile["genre_scores"][name])
        write("summary saved: world={!r} area={!r} name={!r} genres={} "
              "moved={}".format(
                  world, area_id, snapshot["area_name"],
                  len(profile["genre_scores"]),
                  ", ".join("{}={}".format(
                      name, profile["genre_scores"][name]) for name in moved)
                  or "(none)"))
    return True


def _inventory_items(obtainer):
    """売買UIへ渡される在庫実体を配列で取り出す。"""
    inventory = _get(obtainer, "inventory")
    if isinstance(inventory, dict):
        return list(inventory.values())
    if isinstance(inventory, (list, tuple)):
        return list(inventory)
    inner = _get(inventory, "inventory")
    if isinstance(inner, dict):
        return list(inner.values())
    if isinstance(inner, (list, tuple)):
        return list(inner)
    return []


def _inventory_dict(obtainer):
    """ゲームが店主へ持たせている在庫辞書。生成前後の差を見るために使う。"""
    inventory = _get(obtainer, "inventory")
    if isinstance(inventory, dict):
        return inventory
    inner = _get(inventory, "inventory")
    return inner if isinstance(inner, dict) else None


def _selected_major_product(record, scope, owner):
    """店ごとに特産品候補を1つ選ぶ。同じ店では同じ候補になる。"""
    products = record.get("major_products") if isinstance(record, dict) else None
    if not isinstance(products, (list, tuple)) or not products:
        return ""
    owner_id = _short(_get(owner, "id", ""), 120)
    seed = "{}\0{}\0{}".format(scope[0], scope[1], owner_id).encode(
        "utf-8", errors="replace")
    index = int.from_bytes(hashlib.sha256(seed).digest()[:8], "big") % len(products)
    return _clean_item_text(products[index])


def _specialty_messages(snapshot, record, product, value):
    """ゲーム本来の商品スキーマで、特産品をちょうど1個だけ依頼する。"""
    category_lines = []
    for category, subtypes in VALID_SUBTYPES.items():
        category_lines.append("- {}: {}".format(
            category, ", ".join(sorted(subtypes))))
    system_text = (
        "あなたはRPGの店へ地域の特産品を1個だけ追加する担当です。"
        "JSONオブジェクト1個だけを返してください。配列や前後の説明は返しません。\n\n"
        "【必須規則】\n"
        "・item_nameは指定された特産品名と一字一句同じ日本語にする。"
        "高級な、上質な、希少な等の装飾語を足さない。\n"
        "・1個の普通の商品として説明し、別の都市や世界の設定を混ぜない。\n"
        "・item_category.typeとsub_typeは次の許可された組合せだけを使う。\n{}\n"
        "・rarityは common、rare、magical、epic、legendary、mythic のどれか。\n"
        "・item_appearanceは画像生成用の短い英語1文にする。\n"
        "・valueは指定値をそのまま返す。金額や能力値は考えない。"
    ).format("\n".join(category_lines))
    payload = {
        "world_key": snapshot.get("world_key", ""),
        "area_id": snapshot.get("area_id", ""),
        "area_name": snapshot.get("area_name", ""),
        "regional_economy_summary": record.get(
            "regional_economy_summary", ""),
        "major_product": product,
        "value": value,
    }
    return [
        {"role": "system", "content": system_text},
        {"role": "user", "content": json.dumps(
            payload, ensure_ascii=False, indent=2)},
    ]


def _normalize_specialty(raw, product, value):
    """LLMの返答をゲームへ渡せる1商品へ狭める。勝手な分類補正はしない。"""
    data = _raw_dict(raw)
    if not isinstance(data, dict) or not product:
        return None
    category = data.get("item_category")
    if not isinstance(category, dict):
        return None
    item_type = _short(category.get("type"), 60).casefold()
    sub_type = _short(category.get("sub_type"), 80).casefold()
    if item_type not in VALID_SUBTYPES or sub_type not in VALID_SUBTYPES[item_type]:
        return None
    rarity = _short(data.get("rarity"), 40).casefold()
    if rarity not in VALID_RARITIES:
        return None
    description = _short(data.get("description"), 1000)
    appearance = _short(data.get("item_appearance"), 1000)
    if not description or not appearance:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return {
        # 名前はプロフィールの正本を使う。これによりロード後も、余計な
        # アイテム別stateを持たずに特産品マークを再現できる。
        "item_name": product,
        "value": int(round(value)),
        "description": description,
        "item_category": {"type": item_type, "sub_type": sub_type},
        "rarity": rarity,
        "item_appearance": appearance,
    }


def _ask_specialty_item(ctx, write, snapshot, record, product, value):
    """構造化出力を、ゲームのgenerate_item_in_shoppingへ渡すモデルにする。"""
    category_structure = llm.create_structure(
        ctx,
        "RegionalEconomySpecialtyCategory",
        {"type": (str, ...), "sub_type": (str, ...)},
        label="regional economy specialty category",
    )
    if category_structure is None:
        return None
    structure = llm.create_structure(
        ctx,
        "RegionalEconomySpecialtyItem",
        {
            "item_name": (str, ...),
            "value": (int, ...),
            "description": (str, ...),
            "item_category": (category_structure, ...),
            "rarity": (str, ...),
            "item_appearance": (str, ...),
        },
        label="regional economy specialty item",
    )
    if structure is None:
        return None
    raw = llm.ask(
        ctx,
        SPECIALTY_MANAGER_NAME,
        _specialty_messages(snapshot, record, product, value),
        timeout=LLM_TIMEOUT,
        structure=structure,
        max_tokens=1200,
        label="regional economy specialty item",
        write=write,
    )
    normalized = _normalize_specialty(raw, product, value)
    if normalized is None:
        return None
    try:
        return structure(**normalized)
    except Exception:
        validator = getattr(structure, "model_validate", None)
        if callable(validator):
            try:
                return validator(normalized)
            except Exception:
                pass
        ctx.log_exc("regional economy: cannot build specialty item data")
        return None


def _major_product_hit(record, name):
    if not isinstance(record, dict):
        return ""
    return _name_hit(record.get("major_products"), _name_key(name))


def _visible_inventory_context(item_widget, app):
    """マークを出してよい売買画面またはプレイヤー自身の所持品か。"""
    grid = _get(item_widget, "inventory", None)
    if grid is None:
        return ""
    situation = _get(grid, "situation", frames.MISSING)
    if situation == TRADE_SITUATION:
        return "trade"
    if situation is not None:
        return ""
    obtainer = _get(grid, "obtainer", None)
    player = _get(app, "player", None)
    if obtainer is player and player is not None:
        return "own"
    obtainer_id = _short(_get(obtainer, "id", ""), 120)
    player_id = _short(_get(player, "id", ""), 120)
    return "own" if obtainer_id and obtainer_id == player_id else ""


def _trade_owner_side(item_widget, app):
    """売買画面で店主側の商品かを判定する。未知なら反転しない。"""
    grid = _get(item_widget, "inventory", None)
    if grid is None or _get(grid, "situation", frames.MISSING) != TRADE_SITUATION:
        return False
    obtainer = _get(grid, "obtainer", None)
    player = _get(app, "player", None)
    if obtainer in (None, frames.MISSING) or player in (None, frames.MISSING):
        return False
    if obtainer is player:
        return False
    obtainer_id = _short(_get(obtainer, "id", ""), 120)
    player_id = _short(_get(player, "id", ""), 120)
    if obtainer_id and player_id:
        return obtainer_id != player_id
    return True


def _name_key(value):
    if not isinstance(value, str):
        return ""
    return "".join(value.split()).casefold()


def _name_hit(names, item_key):
    """プロフィールの品名が、この商品名を指しているか。

    完全一致か、**プロフィール側の名前が商品名の中に在る**ときだけ当てる。
    LLMが書いた名前どうしは表記が揺れるので（「灼熱の鱗」と「炎竜の鱗」）、
    緩い照合にしても当たらない。当たらないぶんはジャンルの層が受ける。
    1文字の語が広く当たる事故（「鉄」が「鉄の剣」に当たる）を避けるため、
    包含は2文字以上に限る。
    """
    if not isinstance(names, (list, tuple)) or not item_key:
        return ""
    for value in names:
        good = _name_key(_clean_item_text(value))
        if not good:
            continue
        if good == item_key:
            return good
        if len(good) >= 2 and good in item_key:
            return good
    return ""


def _score_for_item(record, detail, name):
    """1品の需給スコアと、その根拠の短い印を返す。

    具体名の層をジャンルより先に見る。プロフィールが名指しした品は、
    ジャンルの平均より強い情報だと考える。
    """
    item_key = _name_key(name)
    hit = _name_hit(record.get("shortage_goods"), item_key)
    if hit:
        return 5, "name/shortage:" + hit
    hit = _name_hit(record.get("surplus_goods"), item_key)
    if hit:
        return 1, "name/surplus:" + hit
    scores = record.get("genre_scores")
    if isinstance(scores, dict) and detail:
        score = _score_value(scores.get(detail))
        if score is not None:
            return score, "genre:" + detail
    return 3, "default"


def _classification_display_score(classification):
    """分類結果の表示用score。無効な返却は未分類の3に戻す。"""
    if not isinstance(classification, dict):
        return 3
    return _score_value(classification.get("score")) or 3


def _classification_price_score(classification):
    """価格補正へ使えるscoreだけを取り出す。特産品は価格を動かさない。"""
    if not isinstance(classification, dict):
        return None
    if classification.get("classification") not in (
            "surplus_good", "shortage_good"):
        return 3
    return _classification_display_score(classification)


def _classification_for_item(state, scope, item):
    """実行中だけ保持する一括分類から、商品1個の結果を引く。"""
    if scope is None or item is None:
        return None
    snapshot = _item_snapshot(item)
    if snapshot is None:
        return None
    key = (scope, snapshot.get("item_key"))
    with state["classification_lock"]:
        result = state["classifications"].get(key)
        if isinstance(result, dict):
            return result
        # 同一内容・内部IDなしの重複品は一括入力時に #2 以降を付ける。
        # 詳細欄には元の内容キーで引き、同じ分類を表示する。
        prefix = snapshot.get("item_key", "") + "#"
        for (saved_scope, saved_key), candidate in state[
                "classifications"].items():
            if (saved_scope == scope and isinstance(saved_key, str) and
                    saved_key.startswith(prefix) and isinstance(candidate, dict)):
                return candidate
    return None


def _remember_classifications(state, scope, snapshots, classifications):
    """一括結果を価格・ホバー表示用の実行時控えへ入れる。"""
    if scope is None or not isinstance(classifications, list):
        return
    with state["classification_lock"]:
        for snapshot, classification in zip(snapshots, classifications):
            if not isinstance(snapshot, dict) or not isinstance(classification, dict):
                continue
            key = snapshot.get("item_key")
            if isinstance(key, str) and key:
                state["classifications"][(scope, key)] = classification


def _classifications_complete(state, scope, snapshots):
    """この売買画面の商品一式を既に検品済みか。"""
    if scope is None or not snapshots:
        return False
    with state["classification_lock"]:
        return all(
            (scope, snapshot.get("item_key")) in state["classifications"]
            for snapshot in snapshots if isinstance(snapshot, dict)
        )


def _price_number(value):
    """買価・売価を正の数として読む。"""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str):
        try:
            number = float(value.strip())
        except ValueError:
            return None
    else:
        return None
    return number if number >= 0 else None


def _regional_multiplier(score):
    """需給スコアを、方向込みの一段の倍率へ変換する。

    倍率そのものをLLMに決めさせない。プレイヤー設定
    （強い変動 / 弱い変動）が意味を持ち続けるようにするため。
    """
    try:
        strong = max(1.0, float(STRONG_FLUCTUATION_MULTIPLIER))
        weak = max(1.0, float(WEAK_FLUCTUATION_MULTIPLIER))
    except (TypeError, ValueError):
        return 1.0
    if score == 5:
        return strong
    if score == 4:
        return weak
    if score == 2:
        return 1.0 / weak
    if score == 1:
        return 1.0 / strong
    return 1.0


def _apply_one_price(state, note, scope, item, attributes, name, score, why):
    """現物1個の買価・売価へ、地域倍率を一度だけ掛ける。

    **129が素の値段へ戻した直後に呼ばれる前提**で書く（`after` で129の
    外側に居るので、129が値付けし直すたびにこちらが掛け直す）。
    現在値が前回こちらが書いた額と同じなら控えた素の値を軸にし、
    違えば129が付け直した今の値を軸にする。どちらでも二重には掛からない。

    価格印は保存の経路からも触るので `price_lock` の中で読み書きする。
    """
    multiplier = _regional_multiplier(score)
    if multiplier == 1.0 or item is None:
        return 0
    changed = 0
    lines = []
    with state["price_lock"]:
        for price_key in PRICE_KEYS:
            if price_key not in attributes:
                continue
            current = _price_number(attributes.get(price_key))
            if current is None:
                continue
            mark_key = (id(item), price_key)
            previous = state["price_marks"].get(mark_key)
            previous_applied = (_price_number(previous.get("applied"))
                                if isinstance(previous, dict) else None)
            if (previous_applied is not None and
                    abs(current - previous_applied) < 0.0001):
                base = _price_number(previous.get("base"))
                if base is None:
                    base = current
            else:
                base = current
            new_price = int(round(base * multiplier))
            if new_price < 0:
                continue
            if abs(current - float(new_price)) >= 0.0001:
                attributes[price_key] = new_price
                changed += 1
                lines.append("regional {} {} score={} {} x{:g}: {:g} -> {}".format(
                    price_key, name, score, why, multiplier, current, new_price))
            state["price_marks"][mark_key] = {
                "scope": scope,
                # 価格印はプロセス内だけの情報。save_game前に戻すため、
                # 現物への参照も保持する（JSONへは書き出さない）。
                "_runtime_item": item,
                "base": base,
                "multiplier": multiplier,
                "applied": new_price,
            }
    # 記録は錠を放してから。ログの書き込みで保存側を待たせない。
    if note is not None:
        for line in lines:
            note(line)
    return changed


def _overlay_item(state, note, scope, record, item, classification=None):
    """1品にこの土地の倍率を掛ける。書き換えた値段の数を返す。"""
    if item is None:
        return 0
    attributes = _get(item, "attributes", {})
    if not isinstance(attributes, dict):
        return 0
    name = _short(_get(item, "name", ""), 240)
    detail = _short(attributes.get("item_detail"), 120).casefold()
    if classification is None:
        score, why = _score_for_item(record, detail, name)
    else:
        score = _classification_price_score(classification)
        if score is None:
            return 0
        why = "llm:{}".format(
            _classification_name(classification.get("classification")))
    return _apply_one_price(state, note, scope, item, attributes,
                            name, score, why)


def _restore_mark(mark_key, mark):
    """印1つを素の値段へ戻す。戻せたら True。"""
    if not isinstance(mark, dict):
        return False
    item = mark.get("_runtime_item")
    price_key = (mark_key[1] if isinstance(mark_key, tuple) and
                 len(mark_key) == 2 else None)
    if item is None or price_key not in PRICE_KEYS:
        return False
    attributes = _get(item, "attributes", {})
    if not isinstance(attributes, dict):
        return False
    current = _price_number(attributes.get(price_key))
    applied = _price_number(mark.get("applied"))
    base = _price_number(mark.get("base"))
    if (current is None or applied is None or base is None or
            abs(current - applied) >= 0.0001):
        return False
    attributes[price_key] = int(round(base))
    return True


def _restore_price_overlays_for_save(state):
    """405の一時価格だけをsave_gameの直前に元の値へ戻す。"""
    restored = []
    marks = state.get("price_marks", {})
    if not isinstance(marks, dict):
        return restored
    with state["price_lock"]:
        for mark_key, mark in list(marks.items()):
            if _restore_mark(mark_key, mark):
                restored.append((mark_key, mark, mark["_runtime_item"],
                                 mark_key[1], _price_number(mark["multiplier"])))
    return restored


def _reapply_price_overlays_after_save(state, restored):
    """save_game後に、画面表示用の405価格だけを現物へ戻す。

    **控えた素の値から組み直す。現在値を軸にしない。**
    軸にすると、保存の実体が走っている間に売買画面や品物欄が
    先に掛け直していた場合、その上へもう一度掛かって倍率が積み上がる
    （並行テストで実際に 100 が 168万まで伸びた）。

    保存の間に誰かが値を動かしていたら、こちらは触らずに降りる。
    次に画面へ出たときに129と405が付け直す。
    """
    marks = state.get("price_marks", {})
    if not isinstance(marks, dict):
        return
    with state["price_lock"]:
        for mark_key, mark, item, price_key, multiplier in restored:
            attributes = _get(item, "attributes", {})
            if not isinstance(attributes, dict) or price_key not in attributes:
                # 所有権移動などで鍵が消えた場合、古い印は破棄する。
                marks.pop(mark_key, None)
                continue
            base = _price_number(mark.get("base"))
            if base is None or multiplier is None:
                marks.pop(mark_key, None)
                continue
            current = _price_number(attributes.get(price_key))
            if current is None or abs(current - round(base)) >= 0.5:
                continue          # 保存中に誰かが動かした。任せる
            new_price = int(round(base * multiplier))
            attributes[price_key] = new_price
            mark["applied"] = new_price


def _drop_foreign_marks(state, scope):
    """いまの取引地点以外の印を、素の値段へ戻してから捨てる。

    別の街の倍率が乗ったままの品を持ち歩かせない。
    **戻さずに印だけ消してはいけない**。戻す手掛かりが消え、
    その街の倍率がセーブへ入る。
    """
    marks = state.get("price_marks")
    if not isinstance(marks, dict):
        return 0
    dropped = 0
    with state["price_lock"]:
        for mark_key, mark in list(marks.items()):
            if isinstance(mark, dict) and mark.get("scope") == scope:
                continue
            _restore_mark(mark_key, mark)
            marks.pop(mark_key, None)
            dropped += 1
    return dropped


def apply(ctx):
    # 節目だけを書く write と、1品ごとの note を分ける。
    # 品物欄は品を選ぶたびに通るので、上限が無いとログが数万行になる。
    write = ctx.logger(LOG_BASENAME)
    note = ctx.logger(LOG_BASENAME, cap=2000)
    state = _store()
    jobs = state["jobs"]
    schedule = ui.scheduler(ctx, "regional economy")
    screen = ui.Screen(ctx, write, tag="regional economy",
                       mark="mod_regional_economy")

    def note_world_identity(app):
        """世界ファイルと現在セーブの食い違いを1回だけ記録する。"""
        if app is None:
            return
        world_name = world_key_of_dict(getattr(app, "world_dict", None), None)
        save_name = world_key_of_dict(getattr(app, "save_data_dict", None), None)
        if not (isinstance(world_name, str) and world_name and
                isinstance(save_name, str) and save_name and
                world_name != save_name):
            return
        signature = (world_name, save_name)
        with state["data_lock"]:
            if signature in state["world_identity_mismatches"]:
                return
            state["world_identity_mismatches"].add(signature)
        selected, _data, source = _active_world_context(app)
        write("world identity mismatch: world_dict={!r} save_data_dict={!r}; "
              "using {!r} from {}".format(
                  world_name, save_name, selected, source))

    def enqueue(snapshot, reason, app=None):
        if not isinstance(snapshot, dict):
            return None
        world = str(snapshot.get("world_key") or "_")
        area_id = str(snapshot.get("area_id") or "")
        if not area_id:
            write("skip: current area has no id")
            return None
        # 405のプロフィールは世界構造の最上位ノードだけが持つ。
        # 子ノードは親の保存済み要約を参照し、独自のLLM生成を行わない。
        current_app = app if app is not None else ui.find_app()
        structure = (_active_world_structure(current_app)
                     if current_app is not None else None)
        if structure is None:
            write("world structure unavailable: no regional profile generated")
            return None
        if _structure_parent(
                structure, snapshot.get("area_name"), area_id) is not None:
            write("child area: no regional profile generated: world={!r} area={!r}"
                  .format(world, area_id))
            return None
        snapshot = dict(snapshot)
        snapshot["world_key"] = world
        snapshot["area_id"] = area_id
        scope = (world, area_id)
        # ここはゲーム側のスレッド（Clock）で走る。
        # **stateファイルは読まない。** 読むのはワーカーの仕事で、
        # ここは覚えている範囲だけで積むかどうかを決める。
        with state["data_lock"]:
            if scope in state["ready"]:
                event = threading.Event()
                event.set()
                return event
            if scope in state["pending"]:
                return state["profile_events"].get(scope)
            event = threading.Event()
            state["profile_events"][scope] = event
            state["pending"].add(scope)
            jobs.put((snapshot, reason))
            write("summary queued: world={!r} area={!r} name={!r} reason={}".format(
                world, area_id, snapshot["area_name"], reason))
        with state["worker_lock"]:
            worker = state.get("worker")
            if worker is not None and worker.is_alive():
                return event
            worker = threading.Thread(
                target=worker_loop,
                name="instantale_mod.regional_economy_profile",
                daemon=True,
            )
            state["worker"] = worker
            worker.start()
        return event

    def worker_loop():
        """LLM待ちと、stateファイルの読み書きを引き受けるスレッド。

        ゲーム側のスレッドはここへ積むだけで、待ちもディスクも通らない。
        新しい注入が来たら降りる（`ctx.superseded()`。TECH.md §3.6.1）。
        自前のスレッドは `revert_all()` では止まらないので、
        積み上がらないよう自分で降りる必要がある。
        """
        while not ctx.superseded():
            try:
                snapshot, reason = jobs.get(timeout=5.0)
            except queue.Empty:
                with state["worker_lock"]:
                    if not jobs.empty():
                        continue
                    state["worker"] = None
                return
            world = snapshot.get("world_key")
            area_id = snapshot.get("area_id")
            scope = (world, area_id)
            try:
                if ctx.superseded():
                    continue
                # stateの読み込みはこのスレッドで行う。
                bucket = _load_bucket(ctx, state, world, write)
                if bucket is None:
                    continue
                if _record_ready(_record_of(bucket, area_id)):
                    with state["data_lock"]:
                        state["ready"].add(scope)
                        first = scope not in state["skip_logged"]
                        state["skip_logged"].add(scope)
                    if first:
                        write("skip existing area: world={!r} area={!r}".format(
                            world, area_id))
                    continue
                write("summary generating: world={!r} area={!r} name={!r}"
                      " reason={}".format(world, area_id,
                                          snapshot["area_name"], reason))
                profile = _ask_profile(ctx, write, snapshot)
                if profile is None:
                    write("summary not saved: invalid LLM result for world={!r} area={!r}".format(
                        world, area_id))
                    continue
                if _save_profile(ctx, state, write, snapshot, profile):
                    with state["data_lock"]:
                        state["ready"].add(scope)
            except Exception:
                ctx.log_exc("regional economy: profile background job failed")
            finally:
                with state["data_lock"]:
                    state["pending"].discard(scope)
                    event = state["profile_events"].pop(scope, None)
                    if event is not None:
                        event.set()
                jobs.task_done()
        with state["worker_lock"]:
            if state.get("worker") is threading.current_thread():
                state["worker"] = None
        write("profile worker stepped down (superseded)")

    def schedule_current(app, reason, delay=0.25):
        """次のフレームで現在地を見て、最上位だけ生成を積む。"""
        def capture():
            target = app if app is not None else ui.find_app()
            try:
                note_world_identity(target)
                dropped = _drop_foreign_marks(state, _scope_of(target))
                if dropped:
                    write("left an area: restored and dropped {} price mark(s)"
                          .format(dropped))
                snapshot = _snapshot(target)
            except Exception:
                ctx.log_exc("regional economy: area snapshot failed")
                return
            if snapshot is None:
                write("area not ready; no summary queued (reason={})".format(reason))
                return
            enqueue(snapshot, reason, app=target)
        schedule(capture, delay=delay)

    def save_without_regional_prices(orig, call_args, call_kwargs):
        """保存処理の実体へ405の一時価格を渡さない。"""
        with state["save_lock"]:
            if state.get("save_in_progress"):
                # `save_game` の内側から保存の実体が呼ばれた場合。
                # 既に外してあるので二重に戻さない。
                return orig(*call_args, **call_kwargs)
            state["save_in_progress"] = True
        restored = []
        try:
            restored = _restore_price_overlays_for_save(state)
            return orig(*call_args, **call_kwargs)
        finally:
            try:
                _reapply_price_overlays_after_save(state, restored)
            finally:
                state["save_in_progress"] = False

    def profile_for(app):
        """いまの取引地点と、そのプロフィール。無ければ `(scope, None)`。

        **ここだけはゲーム側のスレッドで `_load_bucket` を通す。**
        普通はエリア到達のワーカーが先に読んで控えてあるので当たるが、
        ロード直後に店へ直行した場合だけ、この1回がディスクへ行く
        （世界につき1回。以後は控えから返る）。

        読まずに「まだ無い」ことにする手もあるが、
        そうするとロード後の最初の1軒だけ地域の値段が乗らない。
        1回の小さな読み込みと引き換えに、目に見える取りこぼしを作らない。
        """
        scope = _scope_of(app)
        if scope is None:
            return None, None
        bucket = _load_bucket(ctx, state, scope[0], write)
        record = _record_of(bucket, scope[1])
        return scope, (record if _record_ready(record) else None)

    def profile_for_stock(app):
        """最上位エリアの初回品揃えだけ、到着時の生成を待つ。"""
        scope, record = profile_for(app)
        if record is not None or scope is None:
            return scope, record
        snapshot = _snapshot(app)
        event = enqueue(snapshot, "shop stock generation", app=app)
        if event is not None:
            event.wait(timeout=LLM_TIMEOUT)
        if ctx.superseded():
            return scope, None
        return profile_for(app)

    def downstream_economy_context(settlement_name, world_structure):
        """子エリアの概要へ渡す、直上エリアの経済情報を探す。

        親プロフィールの保存済みデータだけを読む。見つからない場合は
        生成を待ったり新しいLLMを呼んだりせず、ゲーム本来の概要を使う。
        """
        app = ui.find_app()
        if app is None:
            return None, None
        note_world_identity(app)
        world = str(_short(_active_world_key(app), 240) or "_")
        structure = (_active_world_structure(app) or world_structure)
        target_id = None
        current_area = ui.current_area(app)
        if current_area is not None:
            target_id = ui.area_id_of(current_area) or None
        parent = _structure_parent(structure, settlement_name, target_id)
        if parent is None:
            return None, None
        parent_id, parent_name = parent
        if not parent_id:
            parent_id = _area_id_by_name(app, parent_name)
        if not parent_id:
            return None, None
        bucket = _load_bucket(ctx, state, world, write)
        record = _record_of(bucket, parent_id)
        context = _economy_context(record)
        if not context:
            return None, None
        return context, (world, parent_id, settlement_name)

    def configured_score_mark(score, trade_owner=False):
        """スコアに応じた印を返す。設定により店主側なら反転する。"""
        if not bool(globals().get("SHOW_SCORE_MARKS", True)):
            return ""
        if score == 3:
            return "-" if bool(globals().get("SCORE_MARK_3", True)) else ""
        if score not in FIXED_SCORE_MARKS:
            return ""
        reverse = (trade_owner and
                   bool(globals().get("REVERSE_TRADE_MARK", True)))
        display_score = (6 - score if reverse else score)
        if display_score not in FIXED_SCORE_MARKS:
            return ""
        # 表示/非表示の設定は商品の本来のスコアに紐付け、
        # 店主側では記号だけを反転する。
        return FIXED_SCORE_MARKS[display_score]

    def clear_item_markers(box):
        """箱の名前ラベルから、スコアや特産品印を消す。"""
        label = frames.attr(box, "name_label", None)
        if label in (None, frames.MISSING):
            return
        previous = frames.attr(label, "_instantale_regional_suffix", "")
        current = frames.text_of(label)
        if (isinstance(previous, str) and previous and
                isinstance(current, str) and current.endswith(previous)):
            try:
                label.text = current[:-len(previous)]
            except Exception:
                pass
        try:
            setattr(label, "_instantale_regional_suffix", "")
        except Exception:
            pass

    def paint_item_markers(box, item_widget, target, record, score,
                           classification=None):
        label = frames.attr(box, "name_label", None)
        if label in (None, frames.MISSING):
            return
        trade_owner = _trade_owner_side(item_widget, ui.find_app())
        score_text = configured_score_mark(score, trade_owner=trade_owner)
        name = _short(_get(target, "name", ""), 240)
        is_specialty = (_major_product_hit(record, name) or
                        (isinstance(classification, dict) and
                         classification.get("classification") == "major_product"))
        specialty_choice = globals().get("SPECIALTY_MARK", "")
        if (specialty_choice not in SPECIALTY_MARK_CHOICES or
                specialty_choice == "なし"):
            specialty_choice = ""
        specialty_text = specialty_choice if is_specialty else ""
        suffix_parts = [text for text in (score_text, specialty_text) if text]
        suffix = (" " + " ".join(suffix_parts)) if suffix_parts else ""
        if not suffix:
            clear_item_markers(box)
            return
        # 別ウィジェットを箱の横へ置かず、ゲーム自身の名前ラベルへ一時連結する。
        # そのため位置・フォント・フォントサイズ・折返しは、元のアイテム名と同じになる。
        previous = frames.attr(label, "_instantale_regional_suffix", "")
        current = frames.text_of(label) or name
        if isinstance(previous, str) and previous and current.endswith(previous):
            current = current[:-len(previous)]
        base_name = current.rstrip()
        if not base_name:
            base_name = name
        try:
            label.text = base_name + suffix
            setattr(label, "_instantale_regional_suffix", suffix)
            # 属性を読むことで、ゲーム側の実際の書体・サイズを経由していることを
            # 明示する。値は変更せず、ユーザー環境の設定をそのまま使う。
            frames.text_of(label, "font_name")
            frames.attr(label, "font_size", None)
        except Exception:
            ctx.log_exc("regional economy: cannot append item marker to name")

    def add_specialty_once(orig, manager, item_data, owner, tier):
        """ゲームの在庫生成1回につき、同じ正規経路でもう1品だけ作る。"""
        app = _get(manager, "app", None) or ui.find_app()
        scope, record = profile_for_stock(app)
        snapshot = _snapshot(app)
        if scope is None or record is None or snapshot is None:
            write("specialty skipped: regional profile is unavailable")
            return
        if (snapshot.get("world_key"), snapshot.get("area_id")) != scope:
            write("specialty skipped: area changed while waiting for profile")
            return
        product = _selected_major_product(record, scope, owner)
        value = _get(item_data, "value", None)
        if not product or isinstance(value, bool) or not isinstance(value, (int, float)):
            write("specialty skipped: product or native value is unavailable")
            return
        generated = _ask_specialty_item(
            ctx, write, snapshot, record, product, value)
        if generated is None:
            write("specialty not generated: invalid LLM result for {!r}".format(
                product))
            return
        inventory = _inventory_dict(owner)
        before = ({id(value) for value in inventory.values()}
                  if isinstance(inventory, dict) else set())
        try:
            # このorigはゲーム本来の1品生成。独自ID・画像・能力値・配置を
            # 405側で再実装せず、通常商品と同じ経路へ任せる。
            orig(manager, generated, owner, tier)
        except Exception:
            ctx.log_exc("regional economy: native specialty generation failed")
            return
        inventory = _inventory_dict(owner)
        added = ([value for value in inventory.values() if id(value) not in before]
                 if isinstance(inventory, dict) else [])
        if len(added) == 1:
            write("specialty generated: {!r} owner={!r} tier={!r}".format(
                product, _get(owner, "id", None), tier))
        else:
            write("WARN specialty native result: expected 1 added item, got {}"
                  .format(len(added)))

    def _classification_for_snapshot(scope, snapshot):
        key = snapshot.get("item_key") if isinstance(snapshot, dict) else None
        if not isinstance(key, str):
            return None
        with state["classification_lock"]:
            result = state["classifications"].get((scope, key))
        return result if isinstance(result, dict) else None

    def _apply_trade_classifications(scope, record, snapshots):
        """一括結果を左右の商品へ価格として反映する。"""
        changed = count = 0
        for snapshot in snapshots:
            if not isinstance(snapshot, dict):
                continue
            item = snapshot.get("_runtime_item")
            classification = _classification_for_snapshot(scope, snapshot)
            if item is None or classification is None:
                continue
            count += 1
            changed += _overlay_item(
                state, note, scope, record, item, classification=classification)
        write("regional overlay: {} price(s) on {} item(s)"
              " world={!r} area={!r}".format(
                  changed, count, scope[0], scope[1]))

    def _open_trade_after_classification(orig, app, left, right, label_text,
                                         situation, extra_args, extra_kwargs,
                                         snapshot, scope, record, snapshots,
                                         pending_key):
        """LLM検品が済んだ後、メインスレッドでだけ売買画面を開く。"""
        def worker():
            classifications = None
            try:
                if not ctx.superseded():
                    classifications = _ask_classification(
                        ctx, write, snapshot, record, snapshots)
            except Exception:
                ctx.log_exc("regional economy: batch classification failed")

            def finish():
                opened = False
                try:
                    if ctx.superseded():
                        write("classification cancelled: newer injection is active")
                        return
                    if _scope_of(app) != scope:
                        write("classification cancelled: area changed while waiting")
                        return
                    if isinstance(classifications, list):
                        _remember_classifications(
                            state, scope, snapshots, classifications)
                        _apply_trade_classifications(scope, record, snapshots)
                        write("classification batch: {} item(s) returned in one JSON"
                              .format(len(classifications)))
                    else:
                        write("classification failed; opening trade window without overlay")
                    screen.busy_off(app, restore=False)
                    orig(app, left, right, label_text, situation,
                         *extra_args, **extra_kwargs)
                    opened = True
                except Exception:
                    ctx.log_exc("regional economy: trade window continuation failed")
                finally:
                    if not opened:
                        try:
                            screen.busy_off(app, restore=False)
                        except Exception:
                            pass
                    with state["classification_lock"]:
                        state["classification_pending"].discard(pending_key)

            schedule(finish, delay=0)

        threading.Thread(
            target=worker,
            name="instantale_mod.regional_economy_item_batch",
            daemon=True,
        ).start()

    def _hold_trade_for_classification(orig, app, left, right, label_text,
                                       situation, extra_args, extra_kwargs,
                                       snapshot, scope, record, snapshots):
        """同じ商品一式の検品中は、売買画面を開かず二重呼び出しを防ぐ。"""
        pending_key = (id(app), scope,
                       tuple(item.get("item_key") for item in snapshots))
        with state["classification_lock"]:
            if pending_key in state["classification_pending"]:
                write("classification already pending; trade window held")
                return True
            state["classification_pending"].add(pending_key)
        try:
            screen.busy_on(app)
            write("classification queued: {} item(s); trade window held"
                  .format(len(snapshots)))
            _open_trade_after_classification(
                orig, app, left, right, label_text, situation, extra_args,
                extra_kwargs, snapshot, scope, record, snapshots, pending_key)
            return True
        except Exception:
            with state["classification_lock"]:
                state["classification_pending"].discard(pending_key)
            ctx.log_exc("regional economy: could not hold trade window")
            return False

    @ctx.wrap("__main__:InstantaleApp.save_game",
              required=False, safe=True)
    def save_game(orig, self, *args, **kwargs):
        """ゲームの通常保存を、405の一時価格を戻してから実行する。"""
        return save_without_regional_prices(
            orig, (self,) + args, kwargs)

    @ctx.wrap("save_world_json:write_obfuscated_json_file",
              required=False, safe=True)
    def write_world_save(orig, *args, **kwargs):
        """保存実体が直接呼ばれる経路でも405の価格を混ぜない。"""
        return save_without_regional_prices(orig, args, kwargs)

    @ctx.wrap(SETTLEMENT_DETAIL_TARGET, required=False, safe=True)
    def create_settlement_detail(orig, world_overview, world_structure,
                                 settlement_name, settlement_overview,
                                 settlement_size, area_description,
                                 include_free_facility=False,
                                 *args, **kwargs):
        """親エリアの地域経済を、子エリアの概要へ一時的に渡す。

        変更するのはゲーム関数へ渡すローカル引数だけ。ゲームのArea、
        world_data、セーブデータ、405のプロフィール保存内容は変更しない。
        """
        try:
            context, source = downstream_economy_context(
                settlement_name, world_structure)
            if context:
                base = settlement_overview or ""
                settlement_overview = (base.rstrip() + "\n\n" + context
                                       if base.strip() else context)
                parent_name = ""
                if isinstance(source, (tuple, list)) and len(source) > 1:
                    parent_name = _short(source[1], 120)
                elif isinstance(source, dict):
                    parent_name = _short(source.get("name", source.get("area_name")), 120)
                write("downstream economy injected: parent={!r} -> child={!r}"
                      .format(parent_name or "?", settlement_name))
        except Exception:
            ctx.log_exc("regional economy: downstream overview injection failed")
        return orig(world_overview, world_structure, settlement_name,
                    settlement_overview, settlement_size, area_description,
                    include_free_facility, *args, **kwargs)

    @ctx.wrap("__main__:MovePhaseManager.move_phase",
              required=False, safe=True)
    def move_phase(orig, self, *args, **kwargs):
        """エリア到達。プロフィール生成の唯一の入口。

        ロードと店開始でも積んでいたが、一度きりの仕事に入口が3つ要らない。
        町に着いてから店に入るまでには間があるので、ここで積めば間に合う。
        ロード直後に店へ直行した場合は売買画面の側が拾う。
        """
        result = orig(self, *args, **kwargs)
        schedule_current(getattr(self, "app", None), "area arrival")
        return result

    @ctx.wrap("__main__:ShoppingStartManagerRemake.execute",
              required=False, safe=True)
    def stock_generation_batch(orig, self, *args, **kwargs):
        """初回生成と312の再入荷を区別せず、ゲームの1回の処理として囲う。"""
        batch = {"manager": id(self), "claimed": False}
        with state["specialty_lock"]:
            previous = state.get("stock_batch")
            state["stock_batch"] = batch
        try:
            return orig(self, *args, **kwargs)
        finally:
            with state["specialty_lock"]:
                if state.get("stock_batch") is batch:
                    state["stock_batch"] = previous

    @ctx.wrap("__main__:ShoppingStartManagerRemake.generate_item_in_shopping",
              required=False, safe=True)
    def observe_stock_item(orig, self, item_data=None,
                           shop_owner_instance=None, item_stock_tier=None,
                           *args, **kwargs):
        """通常商品の生成を先に通し、その一連で特産品を一度だけ追加する。"""
        result = orig(self, item_data, shop_owner_instance, item_stock_tier,
                      *args, **kwargs)
        try:
            claimed = False
            with state["specialty_lock"]:
                batch = state.get("stock_batch")
                if (isinstance(batch, dict) and
                        batch.get("manager") == id(self) and
                        not batch.get("claimed")):
                    # 失敗時に同じ一連の2品目、3品目で再試行しない。
                    # LLMの返答後に通信が切れた場合の重複を避けるほうを優先する。
                    batch["claimed"] = True
                    claimed = True
            if claimed and shop_owner_instance is not None:
                add_specialty_once(
                    orig, self, item_data, shop_owner_instance,
                    item_stock_tier)
        except Exception:
            # 本来の商品は既に生成済み。追加品の失敗を店全体へ伝播させない。
            ctx.log_exc("regional economy: specialty generation failed")
        return result

    @ctx.wrap("__main__:InstantaleApp.toggle_twin_inventory_window",
              required=False, safe=True)
    def inspect_trade_window(orig, self, left_inventory_obtainer=None,
                             right_inventory_obtainer=None,
                             left_label_text=None, situation=None,
                             *args, **kwargs):
        """全商品を一括検品してから、元の売買画面処理を呼ぶ。"""
        try:
            # 所持品の窓（situation=None）と402の受け渡しは素通しする。
            if situation == TRADE_SITUATION:
                scope, record = profile_for(self)
                if scope is None:
                    write("overlay skipped: current area unreadable")
                elif record is None:
                    schedule_current(self, "trade window")
                    write("no regional profile yet for world={!r} area={!r};"
                          " opening as-is".format(*scope))
                else:
                    snapshot = _snapshot(self)
                    snapshots = (_item_snapshots(
                        left_inventory_obtainer, right_inventory_obtainer)
                                 if snapshot is not None else [])
                    if snapshots and snapshot is not None:
                        if _classifications_complete(state, scope, snapshots):
                            _apply_trade_classifications(
                                scope, record, snapshots)
                        else:
                            # LLM待ちは専用ワーカーへ移し、完了まで元の
                            # toggleを呼ばない。したがって売買画面は出ない。
                            if _hold_trade_for_classification(
                                    orig, self, left_inventory_obtainer,
                                    right_inventory_obtainer, left_label_text,
                                    situation, args, dict(kwargs),
                                    snapshot, scope, record, snapshots):
                                return None
        except Exception:
            ctx.log_exc("regional economy: trade window overlay failed")
        return orig(self, left_inventory_obtainer,
                    right_inventory_obtainer, left_label_text,
                    situation, *args, **kwargs)

    @ctx.wrap("scripts.hud.new_hud:ItemDetailBox.update_content",
              required=False, safe=True)
    def item_detail(orig, self, item=None, *args, **kwargs):
        """売買の値段を保ち、許可された所持品画面だけへ需給印を重ねる。

        129は品を選ぶたびに値段を素から組み直す（`detail` 経路）。
        こちらは129の後段なので、その直後に掛け直す。
        """
        app = ui.find_app()
        target = _get(item, "item_instance", None) or item
        context = _visible_inventory_context(item, app)
        scope = record = None
        score = 3
        classification = None
        try:
            if target is not None and context:
                scope, record = profile_for(app)
                if record is not None:
                    attributes = _get(target, "attributes", {})
                    detail = (_short(attributes.get("item_detail"), 120).casefold()
                              if isinstance(attributes, dict) else "")
                    classification = _classification_for_item(
                        state, scope, target)
                    if classification is not None:
                        score = _classification_display_score(classification)
                    elif context == "own":
                        score, _why = _score_for_item(
                            record, detail,
                            _short(_get(target, "name", ""), 240))
                    if context == "trade":
                        # 一括検品済みの商品のみ掛け直す。未検品のまま
                        # 推測で価格を変えると、表示と決済の根拠がずれる。
                        if classification is not None:
                            _overlay_item(state, None, scope, record, target,
                                          classification=classification)
        except Exception:
            ctx.log_exc("regional economy: item detail overlay failed")
        result = orig(self, item, *args, **kwargs)
        try:
            if context and record is not None and target is not None:
                paint_item_markers(self, item, target, record, score,
                                   classification=classification)
                # native Labelのtexture_sizeが更新されるのは次フレームに
                # なることがある。再利用される詳細箱が同じ商品のまま
                # であることを確認してから、商品名末尾の位置を再計算する。
                item_snapshot = _item_snapshot(target)
                item_key = (item_snapshot.get("item_key")
                            if isinstance(item_snapshot, dict) else "")
                setattr(self, "_instantale_regional_item_key", item_key)

                def repaint():
                    if (frames.attr(self, "_instantale_regional_item_key", "")
                            != item_key):
                        return
                    paint_item_markers(self, item, target, record, score,
                                       classification=classification)

                schedule(repaint, delay=0)
            else:
                clear_item_markers(self)
        except Exception:
            ctx.log_exc("regional economy: item marker display failed")
        return result

    ctx.log("regional economy: installed profile, downstream economy context, "
            "batch classification, prices, markers, and specialty stock")
    write("installed: profile + one batch item classification + price overlay + markers"
          " + one specialty per native stock generation + downstream overview context"
          " (genres={} strong={:g} weak={:g})".format(
              len(GENRES), float(STRONG_FLUCTUATION_MULTIPLIER),
              float(WEAK_FLUCTUATION_MULTIPLIER)))
