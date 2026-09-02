# -*- coding: utf-8 -*-
"""都市ごとの地域経済プロフィールを作り、その土地の需給を売買の値段へ乗せるMOD。

## 倍率の軸はジャンル（`item_detail`）

エリアごとに1回だけLLMへ聞き、**32種のジャンルそれぞれに 1〜5 の需給スコア**を
付けてもらう。売買画面では品の `attributes["item_detail"]` からスコアを辞書引きし、
プレイヤー設定の倍率へ変換して買価・売価へ掛ける。**売買画面でLLMは呼ばない。**

品ごとにLLMへ聞く形にしない理由:

- 画面を開くたびに待ちが入る。品数に比例して伸び、返らなければ画面が出ない
- 同じ品が開くたびに違う判定になりうる。**値段が動くと交易が遊びにならない**
  （安く仕入れて高く売るには、その土地の値段が固定である必要がある）

ジャンルを軸にできるのは、ゲーム自身が全ての品に細分を書いているため。
実プレイのログ1382件で `item_detail` の欠けは0件、24種が出現した。

語彙は2段で、生成時の `sub_type` と品に書かれる `item_detail` は綴りが違う
（`small` → `small_weapon`、`herb` → `plant`。GAME.md §2.13.2）。
軸にするのは `item_detail` のほうで、画像フォルダと同じ32種。
sub_type 専用の綴りは持たない。外部ツールが作ったデータは考慮しない。
`129_balance_item_price` の値付け表も同じ鍵で引いている。

## 具体名の層

ジャンルは粗いので、プロフィールが名指しした品だけは上書きする。
`shortage_goods` に名前が当たれば 5、`surplus_goods` なら 1。
文字列の照合だけなのでLLMは要らない。`major_products` では値段を動かさない
（特産品は関連の強さであって、需要・供給の方向を持たないため）。

## 129 との層 ― 包み直しではなく、129の後処理の口へ登録する

**層の置き場所では解けない。** 129は10箇所で値段を書くが、書く時点が地点ごとに
違う ― 画面の2箇所（`toggle_twin_inventory_window` /
`ItemDetailBox.update_content`）は描く前なので元の関数の**前**、
`set_shop_price_for_*` や `normalize_shop_inventory_prices` は**後**。
包みの勝敗は適用順ではなく「元の関数の前に書くか後に書くか」で決まるので、
外側でも内側でも必ず半分の地点で負ける
（実測: 内側に置いていた版で、地域倍率が `shop_owner` /
`normalize_shop_inventory_prices` で9回とも消えていた。買値にだけ乗らない状態）。

そこで129が持っている**値付け直後の口**（`sys._instantale_item_price_post`）へ
登録する。129がどこで書いてもその場で倍率が乗るので、
適用順にも、129がどの経路を通るかにも依存しない。

自前の2フックは残してある。129を切っている構成では素の値段に倍率が掛かるだけの
MODとして動く必要があり、そこは口が存在しないため。
価格印が二重掛けを防ぐので、両方通っても倍率は積み上がらない。
`mod.json` の `"before"` も残す。口が無い版の129と組んだときに、
画面の2箇所だけは内側に居れば従来どおり掛かるため（保険であって、前提ではない）。

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
import os
import queue
import sys
import threading
import typing

from instantale_modloader import frames, llm, ui
from instantale_modloader.state import world_filename, world_key


# ---- 設定（mod.json の default と一致させる。tools/check_mods.py が検査する）
REGIONAL_ECONOMY_SUMMARY_CHARS = 300
REGIONAL_ECONOMY_ITEM_COUNT = 3
REGIONAL_ECONOMY_PROCESSING_STAGES = 2
STRONG_FLUCTUATION_MULTIPLIER = 1.5
WEAK_FLUCTUATION_MULTIPLIER = 1.2
# 安全のための内部値。プレイヤー設定には公開しない。
LLM_TIMEOUT = 120


LOG_BASENAME = "regional_economy.log"
# stateの保存先。他のMODに揃えてMODの主題の名前にする
# （npc_profiles / shop_restock / area_chronicle と同じ形）。
STATE_DIRNAME = "regional_economy"
# shared llmの自動記録をMOD名の1フォルダへ集約する。
MANAGER_NAME = "mod_regional_economy"
STATE_STORE_ATTR = "__instantale_regional_economy_store__"
WORLD_OVERVIEW_CHARS = 2400
AREA_OVERVIEW_CHARS = 2400
LIST_ITEM_CHARS = 240
# 売買の窓だけに掛ける。所持品は None、402の受け渡しは "party_transfer"
# （GAME.md §2.13）。地域の需給は交易の値段の話なので、
# 売り買いをしない窓では触らない。
TRADE_SITUATION = "shop"
BUY_KEY = "買価"
SELL_KEY = "売価"
PRICE_KEYS = (BUY_KEY, SELL_KEY)

# 129 が値段を書いた直後に呼んでもらう口（`sys` の素のリスト。
# 中身は `(名乗り, 関数)`。宣言は 129 の `POST_ATTR` の説明にある）。
# 129 は10箇所で値段を書き、そのうち画面の2箇所しかこちらの層では取れない。
# ここへ登録すれば、129 がどこで書いてもその場で倍率が乗る。
POST_ATTR = "_instantale_item_price_post"
POST_TAG = "405_regional_economy"

# 後処理から記録を出す経路。
# 画面の2箇所（`detail` / `window/*`）はこの MOD 自身のフックが既に通っていて
# 同じ行が出るので、**新しく拾えるようになった経路だけ**を残す。
POST_NOTED = ("shop_owner", "shop_player", "normalize", "generated")

# 品の細分（`attributes["item_detail"]`）を item_type ごとに並べたもの。
#
# 語彙は2段になっていて、混ぜてはいけない（GAME.md §2.13.2）。
#
#   sub_type    生成のときにLLMが選ぶ綴り。ゲームの定数 `ITEM_TYPE_SUBTYPES`
#               （`209_probe_free_facility` が実機から写している）
#   item_detail 品に書かれる綴り。**倍率の軸はこちら**
#
# 間に変換が入り、**画像を引くより前に済んでいる**。
# 実測（`out/item_image.log`）で画像選択へ渡るのは `small` ではなく
# `small_weapon`、`herb` ではなく `plant`。
# したがって `item_detail` に現れるのは画像フォルダ
# `Assets/images/item_candidates_dark/` と同じ32種で、ここもその32種にする。
#
# **sub_type 専用の綴りは置かない**（`small` `medium` `herb` など）。
# `herb` は現役の生成候補だが、品に書かれる段階で `plant` になるため
# `item_detail` としては現れない（実測: consumable と healing_item の
# 300件で `herb` は0件、`plant` は68件）。
# セーブエディタが `herb` を書ける状態にあるが、
# **外部ツールが作ったデータは考慮しない**。
#
# ここへ写しているのは、ModLoaderが単体で配られるため。
# ゲームのファイルを実行時に読むと、ModLoaderだけを入れた環境で動かなくなる。
# 訳語はこちらで付けている。
# 表に無い細分が来ても落ちない（スコア3＝等倍で、値段に触れない）。
#
# `129_balance_item_price` の `RATES` も同じ鍵で引いている。
GENRE_GROUPS = (
    ("weapon", "武器", (
        ("small_weapon", "短剣・小型の武器"),
        ("medium_weapon", "片手剣などの中型の武器"),
        ("large_weapon", "大剣などの大型の武器"),
        ("long_weapon", "槍・長物"),
        ("throwable_weapon", "投擲武器"),
    )),
    ("wearable", "防具・装身具", (
        ("headgear", "兜・頭の防具"),
        ("body_armor", "鎧・胴の防具"),
        ("legwear", "脚の防具"),
        ("gauntlets", "手甲"),
        ("shield", "盾"),
        ("accessory", "装身具・装飾品"),
        ("clothing", "衣服"),
    )),
    ("consumable", "飲食・薬", (
        ("food", "食料"),
        ("drink", "飲み物"),
        ("plant", "薬草・植物"),
        ("mushroom", "きのこ"),
        ("medicine", "薬"),
        ("potion", "調合された薬品"),
    )),
    ("utility", "道具・書物", (
        ("tool", "道具"),
        ("document", "書物・文書"),
        ("scroll", "巻物"),
    )),
    ("material", "素材・財宝", (
        ("creature", "生き物"),
        ("creature_part", "生き物の部位"),
        ("ore", "鉱石"),
        ("metal", "金属・インゴット"),
        ("gem", "宝石"),
        ("treasure", "財宝"),
        ("relic", "遺物"),
        ("scrap", "がらくた"),
        ("magical_material", "魔法の素材"),
        ("liquid_material", "液体の素材"),
        ("other_material", "その他の素材"),
    )),
)

# 引くための平らな表。`item_detail` から訳語を引く。
GENRES = {name: gloss
          for _key, _label, pairs in GENRE_GROUPS
          for name, gloss in pairs}


def _genre_catalog():
    """プロンプトへ載せる、種別ごとに束ねたジャンル一覧。

    平らに32行並べるより、種別で束ねたほうが
    「武器は一通り作れるが薬は輸入」のような筋の通った付け方になる。
    """
    lines = []
    for _key, label, pairs in GENRE_GROUPS:
        lines.append("【{}】".format(label))
        lines += ["・{} : {}".format(name, gloss) for name, gloss in pairs]
    return "\n".join(lines)


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
    world_dict = getattr(app, "world_dict", None)
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
        "world_key": _short(world_key(app), 240),
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
    return (str(_short(world_key(app), 240) or "_"), area_id)


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
        # 生成済みと分かっている取引地点。ゲーム側のスレッドが
        # stateファイルを読まずに「積むか」を決めるための控え。
        "ready": set(),
        "skip_logged": set(),
        "jobs": queue.Queue(),
        "worker": None,
        "data_lock": threading.RLock(),
        "worker_lock": threading.Lock(),
        # 価格印はゲーム側のスレッド（売買画面・品物欄）と
        # 保存の経路の両方から触る。辞書が途中の形で読まれないようにする。
        "price_lock": threading.RLock(),
        "price_marks": {},
        # 保存中の印。`save_game` が内側で保存の実体を呼ぶので、
        # 二重に戻さないための門番。読んで書くまでを割り込ませない。
        "save_lock": threading.Lock(),
        "save_in_progress": False,
    }
    for key, value in defaults.items():
        if key not in found:
            found[key] = value
    if not isinstance(found.get("price_marks"), dict):
        found["price_marks"] = {}
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


def _build_messages(snapshot):
    """1回の呼び出しで、要約とジャンル別の需給スコアを両方もらう。"""
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
    genre_lines = _genre_catalog()
    instruction = (
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
        "【ジャンルの綴りと意味】種別ごとに並べてある。\n{}\n\n"
        "【JSON形式】\n"
    ).format(
        REGIONAL_ECONOMY_SUMMARY_CHARS,
        REGIONAL_ECONOMY_ITEM_COUNT,
        REGIONAL_ECONOMY_ITEM_COUNT,
        REGIONAL_ECONOMY_ITEM_COUNT,
        REGIONAL_ECONOMY_ITEM_COUNT,
        REGIONAL_ECONOMY_PROCESSING_STAGES,
        genre_lines,
    ) + json_format
    payload = json.dumps(snapshot, ensure_ascii=False, indent=2)
    return [{"role": "user", "content": instruction +
             "\n\n【入力資料】\n" + payload}]


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

    **129が素の値段へ戻した直後に呼ばれる前提**で書く（`before` で129の
    内側に居るので、129が値付けし直すたびにこちらが掛け直す）。
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


def _overlay_item(state, note, scope, record, item):
    """1品にこの土地の倍率を掛ける。書き換えた値段の数を返す。"""
    if item is None:
        return 0
    attributes = _get(item, "attributes", {})
    if not isinstance(attributes, dict):
        return 0
    name = _short(_get(item, "name", ""), 240)
    detail = _short(attributes.get("item_detail"), 120).casefold()
    score, why = _score_for_item(record, detail, name)
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

    def enqueue(snapshot, reason):
        if not isinstance(snapshot, dict):
            return
        world = str(snapshot.get("world_key") or "_")
        area_id = str(snapshot.get("area_id") or "")
        if not area_id:
            write("skip: current area has no id")
            return
        snapshot = dict(snapshot)
        snapshot["world_key"] = world
        snapshot["area_id"] = area_id
        scope = (world, area_id)
        # ここはゲーム側のスレッド（Clock）で走る。
        # **stateファイルは読まない。** 読むのはワーカーの仕事で、
        # ここは覚えている範囲だけで積むかどうかを決める。
        with state["data_lock"]:
            if scope in state["ready"] or scope in state["pending"]:
                return
            state["pending"].add(scope)
            jobs.put((snapshot, reason))
            write("summary queued: world={!r} area={!r} name={!r} reason={}".format(
                world, area_id, snapshot["area_name"], reason))
        with state["worker_lock"]:
            worker = state.get("worker")
            if worker is not None and worker.is_alive():
                return
            worker = threading.Thread(
                target=worker_loop,
                name="instantale_mod.regional_economy_profile",
                daemon=True,
            )
            state["worker"] = worker
            worker.start()

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
                jobs.task_done()
        with state["worker_lock"]:
            if state.get("worker") is threading.current_thread():
                state["worker"] = None
        write("profile worker stepped down (superseded)")

    def schedule_current(app, reason, delay=0.25):
        """次のフレームで現在地を見て、控えの掃除と生成の積みを行う。"""
        def capture():
            target = app if app is not None else ui.find_app()
            try:
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
            enqueue(snapshot, reason)
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

    def regional_post(item, attributes, why):
        """129 が値段を書いた**直後**に呼ばれる。その値へ土地の倍率を掛け直す。

        129 は書く時点が地点ごとに違う（画面の2箇所は `orig` の前、
        `set_shop_price_for_*` や `normalize_shop_inventory_prices` は後）。
        包みの層をどちらに置いても半分の地点で負けるので、
        129 が用意した後処理の口を通す。ここなら10箇所すべてに乗る。

        `_apply_one_price` は価格印で二重掛けを避けるので、
        この MOD 自身のフックと重なっても倍率は積み上がらない。
        """
        scope, record = profile_for(ui.find_app())
        if record is None:
            return
        told = note if str(why).split("/")[0] in POST_NOTED else None
        _overlay_item(state, told, scope, record, item)

    # 129 が先か後かは順序で決まらないので、リストは在ればそれを使う。
    # **入れ替えるのではなく中身を書き換える**（129 が握っているのは
    # このリストそのもの。差し替えると向こうから見えなくなる）。
    post_hooks = getattr(sys, POST_ATTR, None)
    if not isinstance(post_hooks, list):
        post_hooks = []
        setattr(sys, POST_ATTR, post_hooks)
    post_hooks[:] = [entry for entry in post_hooks
                     if not (isinstance(entry, tuple) and entry
                             and entry[0] == POST_TAG)]
    post_hooks.append((POST_TAG, regional_post))

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

    @ctx.wrap("__main__:InstantaleApp.toggle_twin_inventory_window",
              required=False, safe=True)
    def inspect_trade_window(orig, self, left_inventory_obtainer=None,
                             right_inventory_obtainer=None,
                             left_label_text=None, situation=None,
                             *args, **kwargs):
        """売買画面の値段へ地域倍率を掛ける。**ここでLLMは呼ばない。**

        倍率は (エリア, ジャンル) の辞書引きなので待つものが無く、
        画面が出るのが遅れない。プロフィールがまだ無ければ補正なしで開き、
        背景の生成だけ積む（次にこの街で開いたときから効く）。
        """
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
                    seen = set()
                    changed = count = 0
                    for obtainer in (left_inventory_obtainer,
                                     right_inventory_obtainer):
                        for item in _inventory_items(obtainer):
                            if item is None or id(item) in seen:
                                continue
                            seen.add(id(item))
                            count += 1
                            changed += _overlay_item(
                                state, note, scope, record, item)
                    write("regional overlay: {} price(s) on {} item(s)"
                          " world={!r} area={!r}".format(
                              changed, count, scope[0], scope[1]))
        except Exception:
            ctx.log_exc("regional economy: trade window overlay failed")
        return orig(self, left_inventory_obtainer,
                    right_inventory_obtainer, left_label_text,
                    situation, *args, **kwargs)

    @ctx.wrap("scripts.hud.new_hud:ItemDetailBox.update_content",
              required=False, safe=True)
    def item_detail(orig, self, item=None, *args, **kwargs):
        """品物欄の値段にも地域の倍率を残す。

        129は品を選ぶたびに値段を素から組み直す（`detail` 経路）。
        こちらは129の内側なので、その直後に掛け直す。
        """
        try:
            target = _get(item, "item_instance", None) or item
            if target is not None:
                scope, record = profile_for(ui.find_app())
                if record is not None:
                    # 品を選ぶたびに通るので、ここからは記録しない
                    # （同じ行が売買画面を開いたときに既に出ている）。
                    _overlay_item(state, None, scope, record, target)
        except Exception:
            ctx.log_exc("regional economy: item detail overlay failed")
        return orig(self, item, *args, **kwargs)

    ctx.log("regional economy: installed genre-based regional price overlay")
    write("installed: profile with {} genre scores + price overlay"
          " (strong={:g} weak={:g}) post-hook on {}".format(
              len(GENRES), float(STRONG_FLUCTUATION_MULTIPLIER),
              float(WEAK_FLUCTUATION_MULTIPLIER),
              ", ".join(entry[0] for entry in post_hooks
                        if isinstance(entry, tuple) and entry) or "-"))
