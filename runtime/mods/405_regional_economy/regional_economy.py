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
降りたワーカーが残した仕事は、次に積みに来た新しい世代が（同じ地点でも）ワーカーを立て直して片付ける。

例外は `profile_for()` の1回だけで、そこは意図して同期に読む（理由はその場に書いた）。

## 値段

**このMODは値段を書かない。** ローダの値段の関所（`prices`。TECH.md §5.9）へ
段を1枚置き、「この品にいくつ掛けるか」だけを答える。129が置く式の上に乗る。

書く側を関所1枚に寄せてあるのは、値段を書く地点が10あって書く時点が
`orig` の前後で混ざっており、MODが別々に包むと相手が必ず半分の地点で負けるため
（TECH.md §3.3.1。実測は VERIFICATION.md §3.19.1）。

段は `temporary` で置くので、**保存の直前は関所が段を外した額を書き、保存後に戻す**。
エリアが変わったら `prices.refresh()` を押して組み直す。
最終額はいつでも式から組み直せるので、このMODは自分が書いた額を控えない
（控えていた頃は、保存中に売買画面が先に掛け直すと倍率が積み上がった
 ― 並行テストで 100 が 168万まで伸びた。組み直す形にしてその経路ごと無くなった）。
"""

import json
import hashlib
import os
import queue
import sys
import threading
import typing

from instantale_modloader import frames, llm, prices, ui
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
# 1/2/4/5 は値段の向きで 1=↓↓、2=↓、4=↑、5=↑↑（`ARROW_MARKS`）。
SHOW_SCORE_MARKS = True
REVERSE_TRADE_MARK = True
# 変動なしの品に何も出さない。動いた品だけが目に入るほうが読みやすい。
# 切っていると「変動なし」と「まだ検品していない」が画面上で同じに見えるので、
# 確かめたいときは ON に戻す。
SCORE_MARK_3 = False
COLOR_SCORE_MARKS = True
MARK_STYLE = "割合（価格+20%）"
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
# 表示は**素の値段から何%動くか**。矢印をやめたのは、上下が「値段の向き」とも
# 「得か損か」とも読めるうえ、店主側で反転するので左右で意味が変わったため
# （2026-09-17）。割合なら数字は常に値段の向きで、反転しない。
# 得か損かは色だけが表す。

#: 街の規模。ゲームが `areas[*]["size"]` に書く語で、実データ3世界を数えると
#: **街はどの世界でもちょうど9件**（village/town/city）、残りは全部 `dungeon`。
#: `connections` の有無とも完全に一致した（街は必ず有り、ダンジョンは必ず空）。
SETTLEMENT_SIZES = ("village", "town", "city")

#: 需給マークの色。**上向き＝自分に得**なので緑、下向き＝損なので赤。
#: 暗い画面に載るので、どちらも明度を上げた色にしてある。
#: 等倍（`-`）には付けない ― 得でも損でもないため。
GAIN_MARK_COLOR = "#7fdf7f"
LOSS_MARK_COLOR = "#ff8c8c"

#: 変動の書き方。`{:+d}` に割合が入る。何の割合かを書いておく。
PERCENT_FORMAT = "（価格{:+d}%）"

#: 表示の形。**`mod.json` の選択肢と同じ綴りにすること**（`check_mods` が見る）。
#: 選ぶ画面で形が分かるよう、綴りそのものに例を入れてある。
MARK_STYLE_PERCENT = "割合（価格+20%）"
MARK_STYLE_ARROW = "矢印（↑↑ ↓↓）"

#: 矢印で出すときの表。**割合と同じく「値段の向き」**にしてある。
#: 得か損かは割合のときと同じく色だけが表すので、左右で引き直さない
#: （向きと色で別々のことを言うと、読み方が2通りに割れる）。
ARROW_MARKS = {1: "↓↓", 2: "↓", 4: "↑", 5: "↑↑"}
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

# LLMが返したジャンルの数がこれを下回るプロフィールは保存しない。
# 半端な表は「鉱石だけ安くて他は全部平常」のような歪んだ経済になる。
# 保存しなければ次に着いたときへ持ち越される。

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


def _area_size(app, area_id):
    """そのエリアの規模。読めなければ None。

    **実行時の `Area.size` は読めない**（`324_` の実機 38/38）ので素データから取る。
    見る順は、いま遊んでいる世界として選ばれた側 → `save_data_dict` →
    `world_dict`（`331_facility_investment` の `area_size` と同じ考え方）。
    """
    # **先に文字列へ均す。** `not area_id` だけで見ると、開始地点の街である
    # id 0 が整数で来た回に空と同じ扱いで落ちる。
    key = "" if area_id is None else str(area_id).strip()
    if app is None or not key:
        return None
    containers = []
    _name, active, _source = _active_world_context(app)
    if isinstance(active, dict):
        containers.append(active)
    for attr in ("save_data_dict", "world_dict"):
        found = getattr(app, attr, None)
        if isinstance(found, dict) and not any(found is c for c in containers):
            containers.append(found)
    for container in containers:
        areas = container.get("areas")
        if not isinstance(areas, dict):
            continue
        entry = areas.get(key)
        if entry is None:
            entry = next((v for k, v in areas.items() if str(k) == key), None)
        if isinstance(entry, dict):
            size = entry.get("size")
            if isinstance(size, str) and size.strip():
                return size.strip().lower()
    return None


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
        # ワーカーを立てた世代の ctx。降りる途中の前の世代のワーカーを見分ける。
        "worker_ctx": None,
        "data_lock": threading.RLock(),
        "worker_lock": threading.Lock(),
        # ゲームの1回の品揃え生成につき、特産品生成を1度だけ確保する。
        "specialty_lock": threading.RLock(),
        "stock_batch": None,
        # 商品分類は永続化しない。売買画面の一括検品が完了した後、
        # 同じ商品内容を再利用するための実行中だけの控え。
        # ゲーム側のスレッド（売買画面・品物欄）と、関所が段を聞きに来る経路の
        # 両方から触るので、辞書が途中の形で読まれないようにする。
        "classification_lock": threading.RLock(),
        "classifications": {},
        "classification_pending": set(),
        # world_dict と現在セーブの名前が一時的に食い違ったことを、
        # 同じロード中に何度も書かないための控え。
        "world_identity_mismatches": set(),
    }
    for key, value in defaults.items():
        if key not in found:
            found[key] = value
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
    """使えるプロフィールか。要約が入っていれば揃っているとみなす。

    **ジャンル表の有無では見ない。** 値付けは品ごとの検品が決めるようになり、
    ジャンル別スコアは誰も読まなくなった（作るのもやめた）。ここで要求すると、
    新しく作った控えが毎回「未完成」になって作り直し続ける。
    古い控えに残っているジャンル表は、読まないだけで害はない。
    """
    if not isinstance(record, dict):
        return False
    summary = record.get("regional_economy_summary")
    return isinstance(summary, str) and bool(summary.strip())


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
    }
    if not profile["regional_economy_summary"]:
        return None
    if any(profile[key] is None for key in (
            "major_industries", "major_products", "surplus_goods",
            "shortage_goods")):
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
    # **内部IDは digest に入れない。** `item_key` が別に前置しており、
    # ここへ入れると内容が同じ品でも digest が変わって控えが引けなくなる。
    identity = {
        "name": name,
        "description": description,
        "item_type": item_type,
        "item_detail": item_detail,
        "rarity": rarity,
    }
    digest = hashlib.sha256(json.dumps(
        identity, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()[:24]
    # `item_key` は**1回の問い合わせの中で品を見分けるため**の鍵なので、
    # 内部IDを含める（同じ内容の品が2つ並ぶことがある）。
    # `content_key` は**控えを引くため**の鍵で、内部IDを含めない。
    # 買うとゲームが同じ内容の品を棚へ作り直す（GAME.md §2.13.1）ので、
    # 内部IDで控えると来店のたびに聞き直すことになる。
    item_key = ("id:{}:{}".format(item_id, digest) if item_id
                else "content:{}".format(digest))
    return {
        "item_key": item_key,
        "content_key": "content:{}".format(digest),
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
    }, ensure_ascii=False)
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
    ).format(
        REGIONAL_ECONOMY_SUMMARY_CHARS,
        REGIONAL_ECONOMY_ITEM_COUNT,
        REGIONAL_ECONOMY_ITEM_COUNT,
        REGIONAL_ECONOMY_ITEM_COUNT,
        REGIONAL_ECONOMY_ITEM_COUNT,
        REGIONAL_ECONOMY_PROCESSING_STAGES,
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
                     REGIONAL_ECONOMY_ITEM_COUNT * LIST_ITEM_CHARS) * 4),
        label="regional economy profile",
        write=write,
    )
    return _normalize_profile(_raw_dict(raw))


def _build_classification_messages(snapshot, record, items):
    """一括検品のメッセージ。

    **固定の指示だけを system に置き、変わるものは全部 user へ。**
    逆にすると、街や棚が変わるたびに system も丸ごと変わるので、
    プロンプトの前半を使い回せない。

    商品に振るのは**入力順の連番**で、内部の鍵は渡さない。
    鍵は1品あたり35文字あり、LLM がそれを丸写しして返す必要があった
    （`item_id` は鍵に含まれているので二重でもあった）。
    """
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
        "- reason:判断理由を日本語で短く記載。推論を行った場合はA→B→C...といった、推論の経由も記載。\n"
        "- n:入力の商品に振られた番号をそのまま返す。\n"
    ).format(rounds, rounds)
    location = json.dumps({
        "world_key": snapshot.get("world_key", ""),
        "area_id": snapshot.get("area_id", ""),
        "area_name": snapshot.get("area_name", ""),
    }, ensure_ascii=False, indent=2)
    item_data = []
    for number, item in enumerate(items, 1):
        item_data.append({
            "n": number,
            "name": item.get("name", ""),
            "description": item.get("description", ""),
            "item_type": item.get("item_type", ""),
            "item_detail": item.get("item_detail", ""),
            "rarity": item.get("rarity", ""),
        })
    user_content = (
        "【取引地点】\n" + location +
        "\n\n【この土地の経済】\n" + json.dumps({
            "summary": record.get("regional_economy_summary", ""),
            "major_industries": record.get("major_industries", []),
            "major_products": record.get("major_products", []),
            "surplus_goods": record.get("surplus_goods", []),
            "shortage_goods": record.get("shortage_goods", []),
        }, ensure_ascii=False, indent=2) +
        "\n\n【売買画面の全商品】\n" +
        json.dumps(item_data, ensure_ascii=False, indent=2)
    )
    return [
        {"role": "system", "content": instruction.rstrip() + "\n"},
        {"role": "user", "content": user_content},
    ]


def _normalize_batch(data, items, record):
    """一括JSONを入力順・全商品1件ずつの結果へ揃える。"""
    raw_items = data.get("items", []) if isinstance(data, dict) else []
    if not isinstance(raw_items, (list, tuple)):
        raw_items = []
    by_number = {}
    by_name = {}
    for raw in raw_items:
        if not isinstance(raw, dict):
            continue
        number = raw.get("n")
        if isinstance(number, bool):
            number = None
        elif isinstance(number, str) and number.strip().isdigit():
            number = int(number.strip())
        if isinstance(number, int) and number not in by_number:
            by_number[number] = raw
        name = raw.get("item_name") or raw.get("name")
        if isinstance(name, str):
            by_name.setdefault(_name_key(name), []).append(raw)
    used = set()
    result = []
    for number, item in enumerate(items, 1):
        raw = by_number.get(number)
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
        "n": (int, ...),
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
        write("summary saved: world={!r} area={!r} name={!r} "
              "products={} surplus={} shortage={}".format(
                  world, area_id, snapshot["area_name"],
                  "/".join(profile["major_products"]) or "(none)",
                  "/".join(profile["surplus_goods"]) or "(none)",
                  "/".join(profile["shortage_goods"]) or "(none)"))
    return True


def _save_classifications(ctx, state, write, world, area_id, fresh):
    """検品結果を世界の控えへ書き足す。**起動をまたいで残す。**

    残さないとプロセスごとに店を1回ずつ聞き直すことになる。
    地域のプロフィールは街ごとに固定なので、同じ内容の品の答えは変わらない。
    """
    if not fresh:
        return False
    with state["data_lock"]:
        bucket = _load_bucket(ctx, state, world, write)
        record = _record_of(bucket, area_id)
        if not isinstance(record, dict):
            return False
        saved = dict(_saved_classifications(record))
        before = len(saved)
        saved.update(fresh)
        if len(saved) == before:
            return False
        areas = dict(bucket.get("areas", {}))
        merged = dict(record)
        merged["classifications"] = saved
        areas[area_id] = merged
        updated = {"world_key": bucket.get("world_key", world), "areas": areas}
        if not ctx.write_json(_state_path(ctx, world), updated, indent=1):
            write("could not save the item classifications for {!r} / {!r}"
                  .format(world, area_id))
            return False
        state["buckets"][world] = updated
        write("classifications saved: {} new, {} known in total"
              " world={!r} area={!r}".format(
                  len(saved) - before, len(saved), world, area_id))
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


def _shop_owner(app):
    """いま居る施設の主。引けなければ None。

    引き方は `312_shop_restock` と同じ（ロード直後の `location` は id の文字列
    なので引き当て直す）。MOD どうしは import しないので、同じ形をここにも置く。
    """
    if app in (None, frames.MISSING):
        return None
    location = _get(getattr(app, "player", None), "location", None)
    if location in (None, frames.MISSING):
        return None
    facility = location
    if isinstance(location, str):
        found = ui.find_facility(ui.current_area(app), location)
        facility = found[0] if isinstance(found, (tuple, list)) and found else None
    if facility is None:
        return None
    owner = getattr(facility, "owner", None)
    if owner is None:
        return None
    if isinstance(owner, (str, int)):
        return ui.character_of(app, str(owner))
    return owner


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


def _classification_for_item(state, scope, record, item):
    """商品1個の検品結果。内容の鍵で引くので、持ち主が移っても同じ答え。"""
    if scope is None or item is None:
        return None
    snapshot = _item_snapshot(item)
    if snapshot is None:
        return None
    return _known_classification(state, scope, record,
                                 snapshot.get("content_key"))


def _remember_classifications(state, scope, snapshots, classifications):
    """一括結果を控えへ入れる。**内容の鍵で持つ**（内部IDでは持たない）。

    入れた `{内容の鍵: 分類}` を返す。呼び側がそれを state へ書き足す。
    """
    fresh = {}
    if scope is None or not isinstance(classifications, list):
        return fresh
    with state["classification_lock"]:
        for snapshot, classification in zip(snapshots, classifications):
            if not isinstance(snapshot, dict) or not isinstance(classification, dict):
                continue
            key = snapshot.get("content_key") or snapshot.get("item_key")
            if isinstance(key, str) and key:
                state["classifications"][(scope, key)] = classification
                fresh[key] = classification
    return fresh


def _saved_classifications(record):
    """state に残っている検品結果 `{内容の鍵: 分類}`。無ければ空。"""
    saved = record.get("classifications") if isinstance(record, dict) else None
    return saved if isinstance(saved, dict) else {}


def _known_classification(state, scope, record, content_key):
    """この品の分類。実行中の控え → state の控え の順に引く。"""
    if not content_key:
        return None
    with state["classification_lock"]:
        found = state["classifications"].get((scope, content_key))
    if isinstance(found, dict):
        return found
    found = _saved_classifications(record).get(content_key)
    return found if isinstance(found, dict) else None


def _unclassified(state, scope, record, snapshots):
    """まだ検品していない品だけを返す。**聞くのはこれだけ。**

    全部まとめて聞き直すと、1品増えただけで店中の品を聞くことになる。
    """
    if scope is None:
        return []
    out = []
    for snapshot in snapshots:
        if not isinstance(snapshot, dict):
            continue
        if _known_classification(
                state, scope, record, snapshot.get("content_key")) is None:
            out.append(snapshot)
    return out



def _score_percent(score):
    """そのスコアで値段が何%動くかの文字列。等倍なら空。

    倍率そのものから作るので、設定（強い変動 / 弱い変動）を変えれば
    表示もついてくる。既定なら 5=+50% / 4=+20% / 2=-17% / 1=-33%。
    """
    multiplier = _regional_multiplier(score)
    percent = int(round((multiplier - 1.0) * 100))
    # **何の%かを表示自体に書く。** 品名の横に `+20%` だけが出ても、
    # 値段の話なのか能力値の話なのか読み取れない（2026-09-17）。
    # 説明欄に値段だけのラベルは無く（`109_` の実測で name / attributes / desc の
    # 3枚）、値段は `attributes` の文へ混ざるので、数字の隣には置けない。
    return PERCENT_FORMAT.format(percent) if percent else ""


def configured_score_mark(score):
    """そのスコアの表示。設定により `（価格+50%）` か `↑↑`。変動なしは `-` か空。

    **左右で反転しない。** どちらの形でもいつも「この街での値段の向き」で、
    自分にとって得か損かは色が表す。
    """
    if not bool(globals().get("SHOW_SCORE_MARKS", True)):
        return ""
    # 綴りに例が入っているので、頭の語だけで見分ける
    # （例の書き方を変えても、保存済みの設定がそのまま効く）。
    if "矢印" in str(globals().get("MARK_STYLE", "")):
        mark = ARROW_MARKS.get(score, "") if _score_percent(score) else ""
    else:
        mark = _score_percent(score)
    if not mark:
        return "-" if bool(globals().get("SCORE_MARK_3", False)) else ""
    return mark

def _mark_rises(mark):
    """その表示が値上がりを指すなら True、値下がりなら False、それ以外は None。

    形（割合か矢印か）を問わずここで見分ける。変動なしの `-` は None。
    """
    if not mark:
        return None
    if mark.startswith("↑"):
        return True
    if mark.startswith("↓"):
        return False
    if "%" not in mark:
        return None
    return "+" in mark


def colored_mark(mark, trade_owner=False):
    """割合を色付きの markup へ包む。色を使わない設定ならそのまま返す。

    **値段が上がることの意味は左右で逆。** 店の品なら余計に払うので損、
    自分の品なら高く売れるので得。だから色は品がどちら側にあるかで決める。
    `REVERSE_TRADE_MARK` を切ると、色は値段の向きだけを表す（左右で同じ）。
    """
    rises = _mark_rises(mark)
    if rises is None or not bool(globals().get("COLOR_SCORE_MARKS", True)):
        return mark
    flip = trade_owner and bool(globals().get("REVERSE_TRADE_MARK", True))
    gains = (not rises) if flip else rises
    return "[color={}]{}[/color]".format(
        GAIN_MARK_COLOR if gains else LOSS_MARK_COLOR, mark)

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
        # 405のプロフィールは**街**（村・町・都市）だけが持つ。
        # ダンジョンや小地点は自分の経済を持たず、親の街の要約を使う。
        #
        # **世界構造（`World.structure`）では判定できない。**
        # 構造は世界生成時の `World` にしか無く、保存される `world_data` の5項目
        # （GAME.md §2.27）に入らない。実セーブを復号して確かめた3世界とも
        # `world_data` の鍵は name / overview / structure_description / story /
        # days_elapsed だけで、`structure` は無い。
        # 構造を条件にしていた版は、ロードした世界で `world structure unavailable`
        # が15回以上出て成功0だった（実機 2026-09-17）。
        current_app = app if app is not None else ui.find_app()
        size = _area_size(current_app, area_id)
        if size is None:
            write("area size unavailable: no regional profile generated:"
                  " world={!r} area={!r}".format(world, area_id))
            return None
        if size not in SETTLEMENT_SIZES:
            write("child area ({}): no regional profile generated:"
                  " world={!r} area={!r}".format(size, world, area_id))
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
                # 積み直さないが、ワーカーが居るかは下で確かめる。前の世代のワーカーが
                # 降りた後も仕事は待ち行列に残るので、起こさずに返すと
                # 別の地点が積まれるまで誰も片付けない（品揃えが待ちの上限まで止まる）。
                event = state["profile_events"].get(scope)
            else:
                event = threading.Event()
                state["profile_events"][scope] = event
                state["pending"].add(scope)
                jobs.put((snapshot, reason))
                write("summary queued: world={!r} area={!r} name={!r} reason={}".format(
                    world, area_id, snapshot["area_name"], reason))
        with state["worker_lock"]:
            worker = state.get("worker")
            owner = state.get("worker_ctx")
            # 前の世代のワーカーはまだ生きていても降りる途中なので、居ないものとして扱う。
            if (worker is not None and worker.is_alive()
                    and not (owner is not None and owner.superseded())):
                return event
            worker = threading.Thread(
                target=worker_loop,
                name="instantale_mod.regional_economy_profile",
                daemon=True,
            )
            state["worker"] = worker
            state["worker_ctx"] = ctx
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
                # 街が変わると段の答えが変わる。関所を押して組み直す
                # （前の街の倍率が乗ったまま持ち歩かせない）。
                changed = prices.refresh("area change")
                if changed:
                    write("left an area: rebuilt {} price(s)".format(changed))
                snapshot = _snapshot(target)
            except Exception:
                ctx.log_exc("regional economy: area snapshot failed")
                return
            if snapshot is None:
                write("area not ready; no summary queued (reason={})".format(reason))
                return
            enqueue(snapshot, reason, app=target)
        schedule(capture, delay=delay)

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
        # 構造はゲームが `create_settlement_detail` の引数で渡してくる。
        # ここは世界生成の最中なので、保存されない構造がそのまま手に入る
        # （素データから引き直す必要も、引ける当ても無い）。
        structure = world_structure
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

    def clear_item_markers(box):
        """箱の名前ラベルから、スコアや特産品印を消す。"""
        label = frames.attr(box, "name_label", None)
        if label in (None, frames.MISSING):
            return
        written = frames.attr(label, "_instantale_regional_text", "")
        plain = frames.attr(label, "_instantale_regional_plain", "")
        current = frames.text_of(label)
        try:
            # **素の文字へ戻してから markup を落とす。**
            # タグの付いた文字列を残したまま markup だけ落とすと、
            # ゲームが塗り直すまで `[color=...]` がそのまま画面に出る
            # （`118_batch_message_render` が実機で踏んだ）。
            if (isinstance(written, str) and written and
                    current == written and isinstance(plain, str)):
                label.text = plain
            elif isinstance(current, str) and isinstance(plain, str) and plain:
                # ゲームが塗り直していたら触らない。印だけ畳む。
                pass
            was = frames.attr(label, "_instantale_regional_markup", None)
            if was is not None and was is not frames.MISSING:
                label.markup = bool(was)
        except Exception:
            ctx.log_exc("regional economy: cannot clear the item marker")
        for attr in ("_instantale_regional_text", "_instantale_regional_plain",
                     "_instantale_regional_markup"):
            try:
                setattr(label, attr, "" if attr.endswith("text")
                        or attr.endswith("plain") else None)
            except Exception:
                pass

    def paint_item_markers(box, item_widget, target, record, score,
                           classification=None):
        label = frames.attr(box, "name_label", None)
        if label in (None, frames.MISSING):
            return
        trade_owner = _trade_owner_side(item_widget, ui.find_app())
        score_text = configured_score_mark(score)
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
        written = frames.attr(label, "_instantale_regional_text", "")
        remembered = frames.attr(label, "_instantale_regional_plain", "")
        current = frames.text_of(label) or name
        if (isinstance(written, str) and written and current == written
                and isinstance(remembered, str) and remembered):
            base_name = remembered          # 前回こちらが書いたもの
        else:
            base_name = current.rstrip()    # ゲームが塗り直したもの
        if not base_name:
            base_name = name
        colored = colored_mark(score_text, trade_owner=trade_owner)
        want_markup = colored != score_text
        try:
            if want_markup:
                from kivy.utils import escape_markup
                # 品名に `[` が入っていてもタグとして解釈させない。
                parts = [escape_markup(base_name), colored]
                if specialty_text:
                    parts.append(escape_markup(specialty_text))
                text = " ".join(parts)
                if frames.attr(label, "_instantale_regional_markup", None) in (
                        None, frames.MISSING):
                    setattr(label, "_instantale_regional_markup",
                            bool(frames.attr(label, "markup", False)))
                label.markup = True
            else:
                text = base_name + suffix
            label.text = text
            setattr(label, "_instantale_regional_text", text)
            setattr(label, "_instantale_regional_plain", base_name)
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

    def _classification_for_snapshot(scope, record, snapshot):
        key = snapshot.get("content_key") if isinstance(snapshot, dict) else None
        return _known_classification(state, scope, record, key)

    def _apply_trade_classifications(scope, record, snapshots):
        r"""検品が済んだので、関所を押して既に触った品を組み直す。

        ここで 0 件でも困らない。**この直後に売買画面を開く**ので、
        関所がその地点で全品を組み直し、そこで段が乗る。
        押しているのは、前にこの街で触った品が控えに残っている場合のため。
        乗ったかどうかは `out\item_price.log` の `+` の付いた行で見る。
        """
        count = sum(1 for snapshot in snapshots
                    if isinstance(snapshot, dict)
                    and _classification_for_snapshot(
                        scope, record, snapshot) is not None)
        changed = prices.refresh("classified")
        write("classified {} item(s); rebuilt {} price(s) already in hand"
              " world={!r} area={!r}".format(
                  count, changed, scope[0], scope[1]))

    def classify_shop(manager, stock_only=False):
        """店と手持ちの品をまとめて検品する。

        ここはゲーム自身が LLM を待つワーカースレッド（`execute`）で、
        `312_shop_restock` の再入荷も同じ場所で待っている。

        **売買画面を押さえて後から開く作りはやめた。** 待機表示は下の画面の
        選択肢を潰すので、潰したまま売買画面を開くと、閉じて戻ったときに
        選択肢が待機表示のまま残る。`restore=True` でも、開くのを1コマ遅らせても
        直らなかった（実機 2026-09-17）。ここで済ませれば画面を潰す場面が
        売買画面と重ならず、後始末はゲーム自身の画面の組み直しに任せられる。
        """
        # `manager` はゲームの店の管理者。売買画面の側から呼ぶときは
        # `None` を渡す（そちらは `app` を直に引ける）。
        app = _get(manager, "app", None) if manager is not None else None
        if app in (None, frames.MISSING):
            app = ui.find_app()
        if app in (None, frames.MISSING):
            return
        scope, record = profile_for(app)
        if scope is None or record is None:
            write("classification skipped: no regional profile for this area yet")
            return
        snapshot = _snapshot(app)
        if snapshot is None:
            return
        owner = _shop_owner(app)
        # **「棚が空」と「店主が引けない」を分ける。** どちらも持ち物が
        # 空に見えるが、前者は品揃えが作られた後に背景が拾える。
        # 後者は背景の側も店主を引けないので、店の品が一度も検品されない。
        # 黙って外れるのを避けるため、引けなかった回は WARN で残す。
        if owner is None:
            write("WARN classification: cannot resolve the shop owner;"
                  " only the player's own goods will be inspected")
        elif stock_only and not _inventory_items(owner):
            # 棚が空＝この後ゲームが品揃えを作る。ここで聞くと手持ちだけを
            # 聞いて、作られた後にもう一度聞くことになる（1回の来店で2回）。
            write("classification deferred: the stock has not been built yet")
            return
        snapshots = _item_snapshots(owner, _get(app, "player", None))
        if not snapshots:
            write("classification skipped: no items to inspect")
            return
        wanted = _unclassified(state, scope, record, snapshots)
        if not wanted:
            write("classification cached: {} item(s)".format(len(snapshots)))
            return
        # **聞くのは未検品のぶんだけ。** 1品増えただけで店中を聞き直さない。
        write("classification needed: {} of {} item(s)".format(
            len(wanted), len(snapshots)))
        snapshots = wanted
        pending_key = (scope, tuple(item.get("content_key") for item in snapshots))
        with state["classification_lock"]:
            if pending_key in state["classification_pending"]:
                return
            state["classification_pending"].add(pending_key)
        def run(after=None):
            try:
                write("classification queued: {} item(s) at the shop start"
                      .format(len(snapshots)))
                classifications = _ask_classification(
                    ctx, write, snapshot, record, snapshots)
                if isinstance(classifications, list):
                    fresh = _remember_classifications(
                        state, scope, snapshots, classifications)
                    write("classification batch: {} item(s) returned in one JSON"
                          .format(len(classifications)))
                    _save_classifications(ctx, state, write, scope[0],
                                          scope[1], fresh)
                else:
                    write("classification failed;"
                          " the trade window opens without overlay")
            except Exception:
                ctx.log_exc("regional economy: batch classification failed")
            finally:
                if after is not None:
                    try:
                        after()
                    except Exception:
                        ctx.log_exc("regional economy: classification cleanup failed")
                with state["classification_lock"]:
                    state["classification_pending"].discard(pending_key)

        screen.busy_on(app)
        # `restore=False`。この後ゲームが店の画面を組み直すので、
        # ここで塗ると一瞬だけ古い選択肢が見える（`312_` と同じ形）。
        run(lambda: screen.busy_off(app, restore=False))


    def regional_for(item, key, price):
        """この土地の需給倍率を1段だけ乗せる。対象外なら None（触らない）。

        **書くのはローダの関所**（`prices.install`）で、ここは「いくつ掛けるか」
        だけを答える。売買の地点も画面の地点も保存の直前も関所が回すので、
        値段印も錠も持たない ― 最終額はいつでも式から組み直せる。

        `temporary=True` で置くので、保存の直前にはこの段が外れる
        （その土地の倍率をセーブへ焼き付けない）。
        """
        app = ui.find_app()
        if app is None:
            return None
        scope, record = profile_for(app)
        if scope is None or record is None:
            return None
        # **未検品の品は動かさない。** 名前とジャンルからの推測で先に乗せると、
        # 検品が済んだ時点で別の倍率へ組み直されて、開いた直後と数秒後で
        # 値段が変わる（実機 2026-09-17。3817 -> 3053）。
        classification = _classification_for_item(state, scope, record, item)
        if classification is None:
            return None
        score = _classification_price_score(classification)
        if score is None:
            return None
        multiplier = _regional_multiplier(score)
        return None if multiplier == 1.0 else price * multiplier

    prices.install(ctx, write)
    prices.adjust(os.path.basename(getattr(ctx, "mod_dir", "") or
                                   "405_regional_economy"),
                  regional_for, temporary=True, write=write)

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
        # **`orig` の前に検品する。** `orig` の中で売買画面が Clock へ積まれ、
        # メインスレッドは `execute` が戻る前にそれを走らせうる
        # （GAME.md §2.13.1）。`finally` に置くと画面が先に開き、
        # LLM が売買画面の上で走る（実機 2026-09-17）。
        try:
            classify_shop(self, stock_only=True)
        except Exception:
            ctx.log_exc("regional economy: shop classification failed")
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

    def _choice_state(app):
        """画面に出ている選択肢の文字。待機表示の後始末が効いたかを見る。"""
        return "choices are {}".format(
            list(frames.attr(app, "to_display_buttons", []) or []))

    def hold_for_classification(orig, app, left, right, label_text, situation,
                                extra_args, extra_kwargs, scope, record,
                                snapshots, wanted):
        """検品が済むまで売買画面を描かせない。**待つのは別スレッド。**

        メインスレッドで待つと Clock が止まり、待機表示のアニメごと画面が
        固まる（実機 2026-09-17。`・・・` が出たまま固まって見えた）。
        ここは待機表示を出して**すぐ戻り**、済んだら元の処理を呼ぶ。

        戻り値が真なら、呼び側は `orig` を呼ばずに降りる。
        """
        snapshot = _snapshot(app)
        if snapshot is None:
            return False
        pending_key = (scope, tuple(item.get("content_key") for item in wanted))
        with state["classification_lock"]:
            if pending_key in state["classification_pending"]:
                return False
            state["classification_pending"].add(pending_key)
        try:
            screen.busy_on(app)
        except Exception:
            ctx.log_exc("regional economy: could not show the busy screen")
            with state["classification_lock"]:
                state["classification_pending"].discard(pending_key)
            return False
        write("classification queued: {} of {} item(s);"
              " the trade window waits".format(len(wanted), len(snapshots)))

        def open_now():
            try:
                orig(app, left, right, label_text, situation,
                     *extra_args, **extra_kwargs)
            except Exception:
                ctx.log_exc("regional economy: could not open the trade window")
            write("trade window opened; " + _choice_state(app))

        def finish(got):
            try:
                if isinstance(got, list):
                    fresh = _remember_classifications(
                        state, scope, wanted, got)
                    write("classification batch: {} item(s) returned in one JSON"
                          .format(len(got)))
                    _save_classifications(ctx, state, write,
                                          scope[0], scope[1], fresh)
                else:
                    write("classification failed;"
                          " the trade window opens without overlay")
                _apply_trade_classifications(scope, record, snapshots)
            except Exception:
                ctx.log_exc("regional economy: classification continuation failed")
            finally:
                with state["classification_lock"]:
                    state["classification_pending"].discard(pending_key)
            try:
                screen.busy_off(app)
            except Exception:
                ctx.log_exc("regional economy: could not clear the busy screen")
            # 塗り直しは次のフレームに落ちる（`ui.Screen.apply_buttons`）。
            # 同じフレームで開くと、塗り直しが売買画面の上に落ちる。
            schedule(open_now, delay=0)

        def worker():
            got = None
            try:
                if not ctx.superseded():
                    got = _ask_classification(
                        ctx, write, snapshot, record, wanted)
            except Exception:
                ctx.log_exc("regional economy: batch classification failed")
            schedule(lambda: finish(got), delay=0)

        threading.Thread(
            target=worker,
            name="instantale_mod.regional_economy_window_batch",
            daemon=True,
        ).start()
        return True

    @ctx.wrap("__main__:InstantaleApp.toggle_twin_inventory_window",
              required=False, safe=True)
    def inspect_trade_window(orig, self, left_inventory_obtainer=None,
                             right_inventory_obtainer=None,
                             left_label_text=None, situation=None,
                             *args, **kwargs):
        """検品済みの結果を値段へ乗せてから、元の売買画面処理を呼ぶ。"""
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
                    # 店の開始時（`execute`）に済ませられるのは、そのとき
                    # 既に棚にあった品だけ。品揃えが作られた回はまだ品が無いので、
                    # 残りをここで片付けてから画面を描かせる
                    # （生成 → 地域価格 → 表示 が1回で通る）。
                    snapshots = _item_snapshots(left_inventory_obtainer,
                                                right_inventory_obtainer)
                    wanted = _unclassified(state, scope, record, snapshots)
                    if wanted and hold_for_classification(
                            orig, self, left_inventory_obtainer,
                            right_inventory_obtainer, left_label_text,
                            situation, args, dict(kwargs),
                            scope, record, snapshots, wanted):
                        return None
                    if snapshots:
                        _apply_trade_classifications(scope, record, snapshots)
        except Exception:
            ctx.log_exc("regional economy: trade window overlay failed")
        return orig(self, left_inventory_obtainer,
                    right_inventory_obtainer, left_label_text,
                    situation, *args, **kwargs)

    @ctx.wrap("__main__:InstantaleApp.close_shopping_window_process",
              required=False, safe=True)
    def closing_shop(orig, self, *args, **kwargs):
        """売買画面から戻ったら、店の選択肢を塗り直す。

        **待機表示を出した回の後始末はここにしか置けない。**
        待機表示は選択肢の文字を `・・・` で上書きして画面へ塗る。解いた時点で
        正しい文字へ塗り直してはいるが、その直後に売買画面が被さるため、
        戻ってきたときに画面へ残っているのは `・・・` のほうになる。

        実機で3点を録って決めた（2026-09-17）。閉じる前・直後・0.5秒後の
        どこでも `buttons` と `to_display_buttons` は正しい文字のままで、
        **データではなく画面だけが古い**。だから塗り直しで足りる。
        閉じるのは `toggle_twin_inventory_window` を通らないので、
        この経路を別に包む必要がある。
        """
        result = orig(self, *args, **kwargs)
        try:
            # `apply_buttons` は必ず次のフレームで塗る（`ui.Screen`）。
            # 閉じる処理と同じ流れで塗ると、ゲーム自身の描画に上書きされる。
            screen.apply_buttons(self, None, "shop closed")
        except Exception:
            ctx.log_exc("regional economy: could not repaint the shop choices")
        return result

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
                    # **検品していない品には何も出さない。**
                    # 名前とジャンルからの推測で出していた頃は、値段が動いて
                    # いないのに「（価格+20%）」と出た（値段の側は検品済みの品
                    # しか動かさない）。表示が嘘をつくので条件を揃えた。
                    classification = _classification_for_item(
                        state, scope, record, target)
                    if classification is not None:
                        score = _classification_display_score(classification)
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
          " (strong={:g} weak={:g} style={!r})".format(
              float(STRONG_FLUCTUATION_MULTIPLIER),
              float(WEAK_FLUCTUATION_MULTIPLIER), MARK_STYLE))
