# -*- coding: utf-8 -*-
"""品の外見文（`item_appearance`）を LLM に書かせ、`128_` の選び方でどの絵が付くかをゲーム抜きで録る。

    python tools\\item_image_probe.py --backend claude                 # Haiku 5.5（work\\key の鍵）
    python tools\\item_image_probe.py --backend local                  # config.json のローカルモデル（Gemma4 系）
    python tools\\item_image_probe.py --backend local --sources restock --genres sf,modern
    python tools\\item_image_probe.py --sheet out\\item_image_probe_<時刻>_claude.jsonl   # 一覧の画像だけ作り直す

## 見るもの

- 外見文が英語で書かれるか（日本語の外見文は埋め込みで [UNK] だけになり、どの絵とも同じ距離になる）
- 10のジャンルの世界（ダークファンタジー・ハイファンタジー・魔法の無い中世・和風・スチームパンク・西部劇・現代・
  ポストアポカリプス・サイバーパンク・SF）で、
  足した絵（`128_` の分類）と素の絵のどれが選ばれるか
- 選ばれた絵を品の名前・外見文と並べた一覧の画像（合っているかは目で見る）

## 頼み（ゲームの記録に合わせているもの）

- `shop`: ゲームの店の品揃え（`shop_item_generator_ordinary`）。記録の頼み文の【エリアの構造】と【現在地】を
  ジャンルの世界のものに入れ替え、価値の数だけ変える。武器と防具を1つずつ
- `quest`: ゲームの依頼の生成（`settlement_quest_generator`）。記録の system はそのまま、user の世界と拠点の欄を
  ジャンルの世界のものにする。敵のドロップの品（消耗品・素材を含む）
- `restock`: `312_` の品揃えの入れ替え。頼み文は `312_` のソースから読む（直したらその文で送る。
  `--restock-old` は版5までの外見文の指示に戻して送る）
- `event`: ゲームのイベントの判定（`field_event_evaluator` / `quest_referee_event_resolve`）の記録のうち、
  品が出た回をそのまま送り直す。`--event-note` は `128_` の手当て（外見文を英語で頼む一文）を当てる
- 型: `shop` / `quest` / `event` は記録の system に埋め込まれた辞書（`105_` の解析）。`restock` は `312_` の欄から組む
- ローカル: `quest_gen_probe.py` と同じ（`105_` の圧縮・`111_` の置換・`127_` の起動引数・`139_` の件数の上限・
  `/apply-template` → `/completion`）
- Claude: `136_` の送信口を通して API へ直接送る（モデルは `claude-haiku-5-5`、推論量は `136_` の既定）。
  system は頼み文、型は `output_config.format`。ゲームの店の頼みは user が無いので `＜取引＞` を置く
  （ゲームが Claude へ送る本文の形は測っていない）

ゲームと LLM サーバは止めておくこと（ローカル）。鍵は `work\\key`（git 管理外）。

## 出力

- `out\\item_image_probe_<時刻>_<backend>.jsonl`: 1品1行（ジャンル・頼み・品名・外見文・日本語か・部位・選ばれた絵・分類・類似度）
- `out\\item_image_probe_<時刻>_<backend>.png`: 選ばれた絵の一覧（ジャンルごと）
- 標準出力: 頼みごとの日本語の外見文の数と、選ばれた分類の内訳
- `--genre-preset`: 世界ごとにジャンルの重みを置いて選ぶ（SF の世界は SF 1・サイバーパンク 0.5 など。`GENRE_PRESET`）
"""

from __future__ import annotations

import argparse
import ast
import collections
import datetime
import glob
import importlib.util
import io
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
RUNTIME_DIR = os.path.join(ROOT, "runtime")
MODS_DIR = os.path.join(RUNTIME_DIR, "mods")
OUT_DIR = os.path.join(ROOT, "out")
MOD_128 = os.path.join(MODS_DIR, "128_item_image_variety")

for path in (RUNTIME_DIR, HERE, os.path.join(MOD_128, "build")):
    if path not in sys.path:
        sys.path.insert(0, path)

import quest_gen_probe as qgp                                        # noqa: E402
from epithet_probe import read_sampling                              # noqa: E402
from llm_ctx_probe import (find_game_dir, pick_build_dir, popen_server, read_live_config,  # noqa: E402
                           running, wait_ready)
from npc_variety_probe import apply_replacements, load_replace_rules  # noqa: E402

CLAUDE_MODEL = "claude-haiku-5-5"
JP = re.compile(r"[\u3040-\u30ff\u4e00-\u9fff]")

# ------------------------------------------------------------------ ジャンルの世界
GENRES = [
    {"id": "dark", "label": "ダークファンタジー",
     "overview": "疫病と戦が続く大陸。灰に覆われた城塞都市が点在し、冒険者ギルドが魔物の討伐と遺跡の探索を請け負う。魔法は血と代償を求める。",
     "state": "北の王国が崩れ、領主どうしの争いと魔物の増加で街道は荒れている。",
     "draft": "一人の冒険者が灰の城塞で魔物の巣を掃うことから物語は始まり、やがて疫病の源である古い神殿へ至る。",
     "quest": ("灰の巣の掃討", "城塞の北の廃坑に棲みついた魔物の群れを討ち、坑道を取り戻す。"),
     "settlement": ("グレイヴホルト", "灰色の石壁に囲まれた城塞都市。鍛冶と傭兵の街として知られる。",
                    "中央の広場を囲んで鍛冶通り・市場・聖堂が並び、外周に兵舎と城門がある。",
                    "灰の降る丘の上の城塞都市。北に廃坑、東に古戦場が広がる。"),
     "shops": [("鉄槌の鍛冶場", "blacksmith", "傭兵向けの剣と斧を打つ鍛冶屋。", "middle-aged man"),
               ("灰狼の防具店", "armor_shop", "革鎧と鎖帷子を扱う防具屋。", "old man"),
               ("灰の市の雑貨店", "general_store", "旅の道具から薬草、保存食まで扱う雑貨屋。", "young woman"),
               ("聖堂の施療院", "medical_facility", "修道女が傷を手当てする施療院。薬と包帯を売る。", "young woman")]},
    {"id": "sf", "label": "SF",
     "overview": "人類が恒星間航路に散らばって数百年が経った宇宙。各星系の軌道ステーションや採掘基地が拠点となり、企業連合と独立系の船団が航路の支配を争っている。冒険者の代わりに、契約屋に登録した請負人が護衛・回収・調査の仕事を受ける。魔法は無く、技術とサイバネティクスが力の源。",
     "state": "企業連合が航路の通行料を引き上げ、独立船団との小競り合いが続いている。",
     "draft": "請負人がステーションの周りで暴走した採掘ドローンを止める仕事から物語は始まり、企業の秘密研究施設へ迫っていく。",
     "quest": ("暴走ドローンの停止", "小惑星帯の採掘区で暴走した警備ドローンの群れを破壊し、制御核を回収する。"),
     "settlement": ("ヴェルナ軌道ステーション", "中心宙域に浮かぶ契約屋の本部を擁する軌道ステーション。",
                    "円環状の構造で、中央の受付ホールを囲むように整備ドック・医院・商店街が並ぶ。",
                    "ヴェルナ星系第二軌道の円環ステーション。周辺の小惑星帯では採掘船が行き交う。"),
     "shops": [("ドック7武器工房", "blacksmith", "請負人向けのエネルギー兵器と銃を扱う工房。", "middle-aged man"),
               ("装甲服ショップ・ハルシオン", "armor_shop", "宇宙服や装甲ジャケットを扱う店。", "young woman"),
               ("ステーション万屋", "general_store", "補給品から携行食、修理部品まで扱う売店。", "old man"),
               ("サイバネ医院", "medical_facility", "義体の調整と治療を行う医院。医療キットを売る。", "middle-aged woman")]},
    {"id": "modern", "label": "現代",
     "overview": "現代の日本によく似た国。地方都市の裏で怪異や事件が起き、探偵事務所や便利屋が警察の手の届かない仕事を請け負う。魔法は無く、銃は厳しく規制されている。",
     "state": "再開発の進む港湾地区で失踪事件が続き、裏社会の組織が動いている。",
     "draft": "便利屋が港の倉庫街で起きた事件を調べることから物語は始まり、やがて街の再開発の裏にある陰謀へ至る。",
     "quest": ("倉庫街の不審者", "港湾地区の倉庫街に出入りする武装した不審者の一団を制圧する。"),
     "settlement": ("東苑市", "港と商店街の並ぶ地方都市。再開発で古い街並みが消えつつある。",
                    "駅前の商店街を中心に、港湾地区・住宅街・繁華街が広がる。",
                    "海沿いの地方都市。港湾地区の倉庫街と、駅前の商店街がある。"),
     "shops": [("アウトドアショップ山猫", "blacksmith", "ナイフや工具、防犯用品を扱うアウトドア用品店。", "middle-aged man"),
               ("古着屋ルート66", "armor_shop", "ジャケットやジーンズを扱う古着屋。", "young man"),
               ("コンビニ東苑駅前店", "general_store", "飲み物から弁当、日用品まで扱うコンビニ。", "young woman"),
               ("東苑ドラッグ", "medical_facility", "薬や救急用品を扱うドラッグストア。", "middle-aged woman")]},
    {"id": "postapoc", "label": "ポストアポカリプス",
     "overview": "大戦で文明が崩れてから百年の荒野。砂に埋もれた都市の廃墟と、水場を囲む要塞集落が点在する。人々はがらくたを漁り、旧時代の銃と手製の武器で略奪者や変異生物と戦う。",
     "state": "水場を巡る集落どうしの争いが激しくなり、略奪者の大集団が南から迫っている。",
     "draft": "流れ者が集落を襲う略奪者を退けることから物語は始まり、旧時代の浄水施設を巡る争いへ至る。",
     "quest": ("錆びた略奪団", "廃墟のハイウェイに陣取る略奪者の一団を討ち、奪われた物資を取り戻す。"),
     "settlement": ("ラストウェル", "枯れかけた井戸を囲む要塞集落。廃車とトタンで壁を築いている。",
                    "中央の井戸を囲んで市場・修理屋・酒場が並び、外周を見張り台と廃車の壁が守る。",
                    "砂漠の中の要塞集落。北に崩れたハイウェイ、東に旧時代の工場跡がある。"),
     "shops": [("ジャンク武器屋", "blacksmith", "旧時代の銃と手製の武器を売る店。", "old man"),
               ("ラグ＆レザー", "armor_shop", "革とタイヤで作った防具を扱う店。", "middle-aged woman"),
               ("井戸端の交易所", "general_store", "缶詰や水、がらくたを物々交換する交易所。", "middle-aged man"),
               ("砂塵の診療所", "medical_facility", "手製の薬と古い医療品を扱う診療所。", "young woman")]},
    {"id": "cyber", "label": "サイバーパンク",
     "overview": "巨大企業が支配する近未来の超高層都市。ネオンの下層区では、義体化した傭兵やハッカーが企業の汚れ仕事を請け負う。魔法は無く、サイバーウェアと電脳が力の源。",
     "state": "企業どうしの抗争が激化し、下層区のギャングが新型の戦闘義体で武装している。",
     "draft": "傭兵が下層区のギャングから盗まれたデータを取り戻すことから物語は始まり、企業の上層部の陰謀へ至る。",
     "quest": ("ネオン街のギャング", "下層区の廃ビルを根城にする義体ギャングを制圧し、盗まれたデータチップを取り戻す。"),
     "settlement": ("ネオ・カグラ下層区", "超高層ビルの足元に広がる、ネオンと雨の下層区。",
                    "高架の下に屋台街・闇医者・ジャンク市場が詰め込まれ、上層への昇降機が中央にある。",
                    "雨の降り続く下層区。廃ビル群と屋台街が入り組む。"),
     "shops": [("ブラックマーケット武器商", "blacksmith", "違法なスマートガンや単分子ブレードを扱う闇市。", "young man"),
               ("ネオン・テーラー", "armor_shop", "防弾ジャケットや光る服を仕立てる店。", "young woman"),
               ("24時間自販ストア", "general_store", "合成食品や飲料、電子部品を売る無人店。", "old man"),
               ("リッパードク", "medical_facility", "義体の取り付けと治療を行う闇医者。", "middle-aged man")]},
    {"id": "wa", "label": "和風",
     "overview": "戦国の世に似た島国。妖が山に棲み、侍や陰陽師が妖退治を請け負う。宿場町には口入れ屋があり、浪人が仕事を求めて集まる。",
     "state": "大名どうしの戦が続き、荒れた山里に妖が増えている。",
     "draft": "浪人が宿場町を荒らす妖を退治することから物語は始まり、やがて妖を操る呪術師との戦いへ至る。",
     "quest": ("峠の鬼退治", "峠道に出る鬼の一党を退治し、旅人の通る道を取り戻す。"),
     "settlement": ("白鷺の宿場町", "街道沿いの宿場町。旅籠と茶屋が軒を連ねる。",
                    "街道に沿って旅籠・茶屋・鍛冶屋が並び、奥に寺と口入れ屋がある。",
                    "山あいの宿場町。北に鬼の出る峠、東に古い寺がある。"),
     "shops": [("刀鍛冶 村正屋", "blacksmith", "刀と槍を打つ刀鍛冶。", "old man"),
               ("具足屋 鷺屋", "armor_shop", "具足と着物を扱う店。", "middle-aged man"),
               ("よろず屋 千鳥", "general_store", "旅の道具や干物、草鞋を扱うよろず屋。", "young woman"),
               ("薬種問屋 白鷺堂", "medical_facility", "生薬と膏薬を扱う薬屋。", "old woman")]},
    {"id": "high", "label": "ハイファンタジー",
     "overview": "エルフやドワーフ、竜が暮らす明るい魔法の大陸。魔法学院と冒険者ギルドがあり、古代の魔法王国の遺跡に宝が眠る。",
     "state": "封じられていた竜王の目覚めが近いと噂され、各国が勇者を募っている。",
     "draft": "冒険者が森の遺跡で魔法の宝珠を見つけることから物語は始まり、竜王の封印を巡る旅へ至る。",
     "quest": ("森の遺跡の魔獣", "エルフの森の古い遺跡に巣くう魔獣を退け、封印の宝珠を守る。"),
     "settlement": ("エルディアの都", "白い塔の並ぶ魔法王国の都。魔法学院と大聖堂がある。",
                    "中央の大聖堂と魔法学院を囲み、職人通りと市場、冒険者ギルドが並ぶ。",
                    "湖のほとりの白い都。北にエルフの森、東に竜の山がある。"),
     "shops": [("ドワーフの鍛冶工房", "blacksmith", "ドワーフが魔法の武器を打つ工房。", "old man"),
               ("妖精の仕立屋", "armor_shop", "魔法のローブと軽い鎧を仕立てる店。", "young woman"),
               ("魔法の道具屋", "general_store", "魔法の小物と冒険の道具を扱う店。", "middle-aged man"),
               ("癒しの神殿", "medical_facility", "神官が傷を癒す神殿。薬と聖水を売る。", "young woman")]},
    {"id": "medieval", "label": "中世（魔法なし）",
     "overview": "魔法の無い中世の王国。領主と騎士が土地を治め、傭兵団が戦を請け負う。疫病と飢饉が人々を苦しめている。",
     "state": "王位継承を巡る内戦が続き、傭兵と盗賊が街道を荒らしている。",
     "draft": "傭兵が村を襲う盗賊団を討つことから物語は始まり、王位を巡る陰謀へ至る。",
     "quest": ("街道の盗賊団", "峠の砦跡に陣取る盗賊団を討ち、奪われた租税を取り戻す。"),
     "settlement": ("ハーフォード", "石の城壁に囲まれた市場町。羊毛の交易で栄える。",
                    "城と教会を中心に、市場広場・ギルド会館・職人の家並みが広がる。",
                    "川沿いの市場町。北に峠の砦跡、南に修道院がある。"),
     "shops": [("鍛冶屋ウィリアム", "blacksmith", "剣と槍、蹄鉄を打つ鍛冶屋。", "middle-aged man"),
               ("甲冑師の店", "armor_shop", "鎖帷子と板金鎧を扱う甲冑師。", "old man"),
               ("市場の雑貨屋", "general_store", "麻袋や蝋燭、保存食を扱う雑貨屋。", "young woman"),
               ("修道院の施療所", "medical_facility", "修道士が薬草で病人を手当てする施療所。", "old man")]},
    {"id": "steam", "label": "スチームパンク",
     "overview": "蒸気機関が広まり始めた群島の商業国家。島々を結ぶ定期船と鉄道会社が力を持ち、港町には職人と船乗りが集まる。冒険者は島の遺跡や航路の厄介ごとを請け負う。",
     "state": "鉄道会社と船主組合の争いが激しくなり、古代の機械遺跡の発掘が進んでいる。",
     "draft": "冒険者が港の倉庫で暴走した蒸気人形を止めることから物語は始まり、古代機械の秘密へ至る。",
     "quest": ("暴走する蒸気人形", "港の地下配管道で暴走した蒸気人形を止め、制御の歯車を回収する。"),
     "settlement": ("ブラスポート", "煙突と歯車の並ぶ港町。蒸気船と鉄道が集まる。",
                    "港の埠頭を中心に、工房街・鉄道駅・商館・酒場が並ぶ。",
                    "霧の港町。地下に古い配管道、沖に機械遺跡の島がある。"),
     "shops": [("歯車工房", "blacksmith", "蒸気銃と機械仕掛けの武器を作る工房。", "middle-aged man"),
               ("真鍮の仕立屋", "armor_shop", "ゴーグルと革のコートを仕立てる店。", "young woman"),
               ("港の雑貨商", "general_store", "工具や油、船旅の品を扱う雑貨商。", "old man"),
               ("蒸気診療所", "medical_facility", "機械仕掛けの医療器具を使う診療所。", "middle-aged woman")]},
    {"id": "western", "label": "西部劇",
     "overview": "開拓時代の荒野。鉄道が伸びる町には保安官と賞金稼ぎがいて、無法者の一味が駅馬車を襲う。銃は回転式拳銃とライフル。",
     "state": "鉄道会社の土地買収に反発する牧場主と、無法者の一味が手を組んでいる。",
     "draft": "賞金稼ぎが駅馬車を襲う一味を追うことから物語は始まり、鉄道会社の陰謀へ至る。",
     "quest": ("峡谷の無法者", "峡谷の廃坑に隠れる無法者の一味を捕らえ、盗まれた金貨を取り戻す。"),
     "settlement": ("ダストクリーク", "鉄道の駅を中心にできた荒野の町。酒場と保安官事務所がある。",
                    "駅前の大通りに酒場・雑貨店・銀行・保安官事務所が並び、外れに牧場がある。",
                    "赤い岩の荒野の町。北に峡谷の廃坑、東に先住民の聖地がある。"),
     "shops": [("ガンスミス", "blacksmith", "回転式拳銃とライフルを扱う銃砲店。", "old man"),
               ("仕立屋と馬具店", "armor_shop", "革のコートと帽子、馬具を扱う店。", "middle-aged woman"),
               ("ダストクリーク雑貨店", "general_store", "缶詰や弾薬、開拓の道具を扱う雑貨店。", "middle-aged man"),
               ("町医者", "medical_facility", "鞄一つで往診する町医者。薬と包帯を売る。", "old man")]},
]


#: `--genre-preset` で置く世界のジャンルの重み（遊ぶ側がワールド個別設定で置く想定の値）。event は置かない
GENRE_PRESET = {"dark": {"darkfantasy": 1.0, "medieval": 1.0, "fantasy": 1.0}, "sf": {"sf": 1.0, "cyber": 0.5}, "modern": {"modern": 1.0},
                "postapoc": {"postapoc": 1.0, "modern": 0.5}, "cyber": {"cyber": 1.0, "sf": 0.5}, "wa": {"wa": 1.0},
                "high": {"highfantasy": 1.0, "medieval": 1.0, "fantasy": 1.0}, "medieval": {"medieval": 1.0}, "steam": {"earlymodern": 1.0, "medieval": 0.3},
                "western": {"earlymodern": 1.0, "postapoc": 0.3}}


def area_dict(genre):
    """ゲームの店の頼みの【エリアの構造】（ゲームは辞書の repr をそのまま書く）。"""
    name, overview, _structure, _desc = genre["settlement"]
    locations = {"{} - 入口".format(name): {"description": "", "type": "entrance", "npcs": {}}}
    for i, (shop, kind, desc, who) in enumerate(genre["shops"]):
        locations[shop] = {"description": desc, "type": kind,
                           "npcs": {"店主{}".format(i + 1): {"profile": desc + "その店の主。", "category": who}}}
    return {"name": name, "overview": overview, "wards": {"": {"overview": "", "locations": locations}}}


# ------------------------------------------------------------------ 頼み
def latest_record(game_dir, manager, need=None):
    paths = sorted(glob.glob(os.path.join(str(game_dir), "output_data", "*", "*", manager, "*.json")),
                   key=os.path.getmtime, reverse=True)
    for path in paths:
        with io.open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        text = "\n".join(m["content"] for m in data["messages"] if isinstance(m.get("content"), str))
        if need is None or need in text:
            return data
    raise SystemExit("記録が無い: {}".format(manager))


def shop_requests(game_dir, genres):
    record = latest_record(game_dir, "shop_item_generator_ordinary", "必ず武器と防具をそれぞれ一つずつ生成する")
    fmt, prompt = record["messages"][0]["content"], record["messages"][1]["content"]
    head = prompt.split("【エリアの構造】", 1)[0]
    out = []
    for genre in genres:
        for value, (shop, _kind, _desc, _who) in zip((12, 30, 20, 8), genre["shops"]):
            text = re.sub(r"「\d+」を指定", "「{}」を指定".format(value), head)
            text += "【エリアの構造】\n{!r}\n\n【現在地】\n{}".format(area_dict(genre), shop)
            out.append({"genre": genre["id"], "source": "shop", "where": shop,
                        "messages": [{"role": "system", "content": fmt}, {"role": "system", "content": text}],
                        "claude_user": "＜取引＞"})
    return out


def quest_user(genre, difficulty):
    name, overview, structure, desc = genre["settlement"]
    title, quest_overview = genre["quest"]
    return ("【世界のデータ】\n- 世界の概要: {o}\n\n【世界のストーリー】\n- 世界の現状: {s}\n- ストーリーの流れの草稿: {d}\n"
            "- 現在の段階: 2/5\n\n【クエストの概要】\n- title: {t}\n- overview: {q}\n\n【クエストエリアのデータ】\n"
            "- settlement_name: {n}\n- settlement_overview: {so}\n- settlement_structure_description: {st}\n"
            "- area_description: {ad}\n- 想定段階: ゲーム序盤\n\n【補足】\n- このクエストの難易度は{k}/70である。").format(
        o=genre["overview"], s=genre["state"], d=genre["draft"], t=title, q=quest_overview, n=name, so=overview,
        st=structure, ad=desc, k=difficulty)


def quest_requests(game_dir, genres):
    record = latest_record(game_dir, "settlement_quest_generator")
    systems = [m for m in record["messages"] if m["role"] == "system"]
    out = []
    for genre in genres:
        for difficulty in (6, 35):
            out.append({"genre": genre["id"], "source": "quest", "where": "難易度{}".format(difficulty),
                        "messages": systems + [{"role": "user", "content": quest_user(genre, difficulty)}]})
    return out


#: `312_` 版5までの外見文の指示（`--restock-old` で比べる）
RESTOCK_OLD = "- item_appearance: 見た目を日本語で短く（画像の選択に使う）。\n"


def restock_template(old=False):
    """`312_` の頼み文のひな形と、品の種類の表（ソースから読む。直したらその文になる）。"""
    path = os.path.join(MODS_DIR, "312_shop_restock", "shop_restock.py")
    source = io.open(path, encoding="utf-8").read()
    literal = source.split("text = (", 1)[1].split(").format(", 1)[0]
    template = ast.literal_eval("(" + literal + ")")
    if old:
        template = re.sub(r"- item_appearance: [^\n]*\n", lambda _m: RESTOCK_OLD, template, count=1)
    tree = ast.parse(source)
    consts = {node.targets[0].id: ast.literal_eval(node.value) for node in tree.body
              if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name)
              and node.targets[0].id in ("ITEM_TYPES", "RARITIES")}
    return template, consts["ITEM_TYPES"], consts["RARITIES"]


RESTOCK_FIELDS = (("item_name", "string"), ("description", "string"), ("item_type", "string"),
                  ("item_sub_type", "string"), ("value", "integer"), ("item_appearance", "string"), ("rarity", "string"))


def restock_schema():
    """`312_` の `llm.create_structure` が作る型（pydantic の `model_json_schema()` と同じ形）。"""
    item = {"properties": {k: {"title": k.replace("_", " ").title(), "type": t} for k, t in RESTOCK_FIELDS},
            "required": [k for k, _t in RESTOCK_FIELDS], "title": "ShopStockItem", "type": "object"}
    return {"$defs": {"ShopStockItem": item},
            "properties": {"items": {"items": {"$ref": "#/$defs/ShopStockItem"}, "title": "Items", "type": "array"}},
            "required": ["items"], "title": "ShopStock", "type": "object"}


def restock_requests(genres, old=False):
    template, item_types, rarities = restock_template(old)
    types = "、".join("{}: {}".format(t, "/".join(subs)) for t, subs in item_types.items())
    out = []
    for genre in genres:
        name, overview, _structure, desc = genre["settlement"]
        for shop, kind, shop_desc, _who in (genre["shops"][2], genre["shops"][3]):
            text = template.format(n=6, world=genre["label"] + "の世界", area=name, area_desc=desc + overview,
                                   shop=shop, kind=kind, shop_desc=shop_desc, types=types,
                                   values=[4, 8, 12, 6, 15, 3], rarities="/".join(rarities), old="無し")
            fmt = "必ず日本語で、以下のjson形式で出力すること: {!r}".format(restock_schema())
            out.append({"genre": genre["id"], "source": "restock", "where": shop, "schema": restock_schema(),
                        "messages": [{"role": "system", "content": fmt}, {"role": "system", "content": text},
                                     {"role": "user", "content": "＜入れ替え＞"}]})
    return out


EVENT_MANAGERS = ("field_event_evaluator", "quest_referee_event_resolve")
FORMAT_HEAD = "以下のjson形式で出力すること"


def event_note():
    """`128_` のイベントの外見文の手当て（`event_item_note`）。ソースから読む。"""
    spec = importlib.util.spec_from_file_location("iiv_probe", os.path.join(MOD_128, "item_image_variety.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.event_item_note


def event_requests(game_dir, runs, with_note):
    """ゲームのイベントの判定の記録のうち、品が出た回をそのまま送り直す（`--event-note` は 128_ の手当てを当てる）。
    型が埋め込まれていない記録（Claude で動いた回）は、同じ判定の別の記録の型の指示を先頭に足す。"""
    note = event_note() if with_note else None
    out = []
    for manager in EVENT_MANAGERS:
        paths = sorted(glob.glob(os.path.join(str(game_dir), "output_data", "*", "*", manager, "*.json")),
                       key=os.path.getmtime)
        records = [json.load(io.open(p, encoding="utf-8")) for p in paths]
        fmt = next((m for r in records for m in r["messages"]
                    if isinstance(m.get("content"), str) and FORMAT_HEAD in m["content"]), None)
        for path, record in zip(paths, records):
            if "item_appearance" not in json.dumps(record.get("response"), ensure_ascii=False):
                continue
            messages = [{"role": m["role"], "content": m["content"]} for m in record["messages"]]
            if fmt and not any(FORMAT_HEAD in m["content"] for m in messages):
                messages = [dict(fmt)] + messages
            if note:
                texts = note([m["content"] for m in messages])
                if texts:
                    messages = [dict(m, content=t) for m, t in zip(messages, texts)]
            for run in range(runs):
                out.append({"genre": "event", "source": "event",
                            "where": "{} {} #{}".format(manager, os.path.basename(path), run + 1),
                            "messages": messages})
    return out


# ------------------------------------------------------------------ 型
def embedded_schema(messages, compact_mod):
    for message in messages:
        text = message["content"]
        start = compact_mod.find_schema_start(text)
        if start >= 0:
            schema, _end = compact_mod.parse_literal(text, start)
            if isinstance(schema, dict):
                return schema
    raise SystemExit("型が頼み文から読めない")


#: SDK が型から外して説明文へ書き移す制約（API が受けないもの。`136_` 版5の手当ての前提）
MOVED = ("maxItems", "maximum", "minItems", "minimum")


def claude_schema(node):
    """SDK が API へ渡す形に寄せる（object は additionalProperties: false、const は値1つの enum、
    数と件数の範囲は `{maximum: 5, minimum: 1}` の形で description へ）。"""
    if isinstance(node, dict):
        out = {k: claude_schema(v) for k, v in node.items() if k != "const" and k not in MOVED}
        if "const" in node:
            out["enum"] = [node["const"]]
        moved = ["{}: {}".format(k, node[k]) for k in MOVED if k in node]
        if moved:
            out["description"] = "{" + ", ".join(moved) + "}"
        if out.get("type") == "object" or "properties" in out:
            out["additionalProperties"] = False
        return out
    if isinstance(node, list):
        return [claude_schema(v) for v in node]
    return node


# ------------------------------------------------------------------ 送信
class ClaudeSender(object):
    """`136_` の Claude の送信口を通して API へ直接送る（`136_` の差し替え・型の手当てがそのまま効く）。"""

    def __init__(self):
        folder = os.path.join(MODS_DIR, "136_cloud_model_override")
        spec = importlib.util.spec_from_file_location("cmo_probe", os.path.join(folder, "cloud_model_override.py"),
                                                      submodule_search_locations=[folder])
        mod = importlib.util.module_from_spec(spec)
        sys.modules["cmo_probe"] = mod
        spec.loader.exec_module(mod)
        side = sys.modules["cmo_probe.claude_side"]
        self.key = io.open(os.path.join(ROOT, "work", "key"), encoding="utf-8").read().strip()
        mod.CLAUDE_MODEL, mod.CLAUDE_CUSTOM, mod.CLAUDE_CACHE = CLAUDE_MODEL, "", "off"

        sender = self

        class Ctx(object):
            generation = "probe"
            mod_dir = folder
            hooks = {}
            lines = []

            def on_ready(self, fn, **kwargs):
                return True

            def log(self, text, **kwargs):
                sender.lines.append(text)

            def log_exc(self, text, **kwargs):
                sender.lines.append(text)

            def wrap(self, target, **kwargs):
                def deco(fn):
                    self.hooks[target] = fn
                    return fn
                return deco

        self.lines = []
        ctx = Ctx()
        mod.apply(ctx)
        self.post = ctx.hooks[side.POST]
        self.usage = collections.Counter()

    def _send(self, _self, path, *args, **kwargs):
        body = kwargs["body"]
        req = urllib.request.Request("https://api.anthropic.com/v1/messages", data=json.dumps(body).encode("utf-8"),
                                     headers={"x-api-key": self.key, "anthropic-version": "2023-06-01",
                                              "content-type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=600) as resp:
                raw = json.load(resp)
        except urllib.error.HTTPError as exc:
            raise RuntimeError("HTTP {} {}".format(exc.code, exc.read()[:300].decode("utf-8", "replace")))

        class Block(object):
            def __init__(self, data):
                self.type, self.text = data.get("type"), data.get("text")

        class Message(object):
            def __init__(self, data):
                self.raw = data
                self.content = [Block(b) for b in data.get("content", [])]
        return Message(raw)

    def generate(self, request, schema):
        systems = [m["content"] for m in request["messages"] if m["role"] == "system"]
        users = [m for m in request["messages"] if m["role"] == "user"]
        # ゲームの型の指示（system の1つ目）は Claude では output_config に移る
        system = systems[-1]
        messages = [{"role": "user", "content": m["content"]} for m in users] or \
            [{"role": "user", "content": request.get("claude_user") or "＜生成＞"}]
        body = {"max_tokens": 16384, "messages": messages, "model": "claude-sonnet-5", "system": system,
                "thinking": {"type": "disabled"},
                "output_config": {"format": {"type": "json_schema", "schema": claude_schema(schema)}}}

        class Client(object):
            base_url = "https://api.anthropic.com"
        started = time.time()
        reply = self.post(self._send, Client(), "/v1/messages", body=body, options={})
        raw = reply.raw
        for k in ("input_tokens", "output_tokens"):
            self.usage[k] += (raw.get("usage") or {}).get(k) or 0
        text = "".join(b.text or "" for b in reply.content if b.type == "text")
        return {"seconds": round(time.time() - started, 1), "content": text, "model": raw.get("model"),
                "stop": raw.get("stop_reason")}


class LocalSender(object):
    """`quest_gen_probe.py` と同じ起こし方・送り方（`105_` の圧縮と `111_` の置換も同じ）。"""

    def __init__(self, game_dir, port, base_url=None, log_path=None, pattern=None):
        settings = read_live_config(game_dir).get("ai_setting") or {}
        self.sampling = read_sampling(settings)
        self.compact = qgp.load_compact_module()
        self.replace = load_replace_rules()
        self.proc = self.handle = None
        self.model = None
        if base_url:
            self.base = base_url.rstrip("/")
            return
        busy = running("instantale.exe", "llama-server.exe")
        if busy:
            raise SystemExit("ゲームか LLM サーバが動いている: {}".format(", ".join(busy)))
        pattern = pattern or ((settings.get("local_model_setting") or {}).get("local_llm") or {}).get("name") or ""
        models_dir = os.path.join(str(game_dir), "runtime", "models", "llama_cpp")
        found = sorted(n for n in os.listdir(models_dir) if n.lower().endswith(".gguf") and pattern.lower() in n.lower())
        if len(found) != 1:
            raise SystemExit("モデルが1つに決まらない: {}".format(found))
        self.model = found[0]
        backend = (settings.get("local_model_setting") or {}).get("llm_backend", "llama-cpp-completion-cuda")
        server = pick_build_dir(game_dir, backend) / "llama-server.exe"
        self.handle = io.open(log_path, "w", encoding="utf-8", errors="replace")
        command = [str(server), "-m", os.path.join(models_dir, found[0]), "--host", "127.0.0.1", "--port", str(port),
                   "--temp", str(self.sampling["temperature"]), "--top-p", str(self.sampling["top_p"]),
                   "--top-k", str(self.sampling["top_k"])] + qgp.SERVER_FLAGS
        command, _changes = qgp.load_speed_module().rewrite_argv(command)
        print("  モデル: {}（起動中）".format(found[0]), flush=True)
        self.proc = popen_server(command, stdout=self.handle, stderr=subprocess.STDOUT)
        if not wait_ready(port, 600, self.proc):
            raise SystemExit("サーバが起動しなかった: {}".format(log_path))
        self.base = "http://127.0.0.1:{}".format(port)

    def generate(self, request, schema):
        sent = []
        for message in request["messages"]:
            result = self.compact.compact_embedded_schema(message["content"])
            sent.append({"role": message["role"], "content": result[0] if result else message["content"]})
        sent, _hits = apply_replacements(self.replace, sent)
        got = qgp.generate(self.base, sent, schema, self.sampling, 900)
        return {"seconds": got["seconds"], "content": got["content"], "model": self.model, "stop": got["stop_type"]}

    def close(self):
        if self.proc is not None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=30)
            except Exception:
                self.proc.kill()
        if self.handle is not None:
            self.handle.close()


# ------------------------------------------------------------------ 品の取り出しと絵の選択
WEAPON = {"small", "medium", "large", "long", "throwable"}


def detail_of(item_type, sub_type):
    if item_type == "weapon" and sub_type in WEAPON:
        return sub_type + "_weapon"
    return {"herb": "plant"}.get(sub_type, sub_type)


def items_of(data, source):
    """応答から品を取り出す: `(名前, 説明, 外見文, item_type, sub_type)`。"""
    out = []

    def walk(node):
        if isinstance(node, dict):
            if isinstance(node.get("item_appearance"), str) and "item_name" in node:
                cat = node.get("item_category") or {}
                if source == "restock":
                    kind, sub = node.get("item_type"), node.get("item_sub_type")
                else:
                    kind, sub = cat.get("type"), cat.get("sub_type")
                out.append((node.get("item_name"), node.get("description") or "", node["item_appearance"], kind, sub))
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)
    walk(data)
    return out


class Picker(object):
    """`128_` の選び方（既定の設定）を再現する。使った回数は1回の試しの中で数える。"""

    def __init__(self, game_dir):
        from minibert import MiniLM
        import numpy as np
        self.np = np
        self.model = MiniLM()
        source = io.open(os.path.join(MOD_128, "item_image_variety.py"), encoding="utf-8").read()
        consts = {n.targets[0].id: n.value for n in ast.parse(source).body
                  if isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Name)}
        self.top_k = ast.literal_eval(consts["TOP_K"])
        self.floor = ast.literal_eval(consts["SIM_FLOOR"])
        self.min_sim = ast.literal_eval(consts["SEPARATE_MIN_SIM"])
        self.margin = ast.literal_eval(consts["SEPARATE_MARGIN"])
        self.cross = ast.literal_eval(consts["CROSS_SLOT"])
        self.cross_below = ast.literal_eval(consts["CROSS_SLOT_BELOW"])
        self.cross_min = ast.literal_eval(consts["CROSS_SLOT_MIN"])
        self.cross_margin = ast.literal_eval(consts["CROSS_SLOT_MARGIN"])
        self.cross_slots = ast.literal_eval(consts["CROSS_SLOTS"])
        self.cross_prefix = ast.literal_eval(consts["CROSS_PREFIX"])
        self.cats = json.load(io.open(os.path.join(MOD_128, "categories.json"), encoding="utf-8"))
        self.emb_dir = os.path.join(str(game_dir), "Data", "item_embeddings")
        self.img_dir = os.path.join(str(game_dir), "Assets", "images", "item_candidates_dark")
        self.pools, self.usage = {}, collections.Counter()
        self.bonus_max = ast.literal_eval(consts["GENRE_BONUS"])
        self.world_genres = {}          # {ジャンル: 重み}。空なら素の選び方（128_ の既定）
        spec = importlib.util.spec_from_file_location("iiv_picker", os.path.join(MOD_128, "item_image_variety.py"))
        body = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(body)
        self.genre_stock, self.genre_weights = body.read_genre_weights(MOD_128, os.path.join(OUT_DIR, "_no_user_weights.json"))

    def bonus(self, cats):
        """`128_` と同じ足し方（世界のジャンルの重み × 分類のジャンル内の重み × GENRE_BONUS、一番大きい値）。"""
        if not any(w > 0 for w in self.world_genres.values()):
            return None
        out = []
        for cat in cats:
            table = self.genre_stock if cat in ("stock", "extra") else self.genre_weights.get(cat, {})
            out.append(self.bonus_max * max([self.world_genres.get(g, 0.0) * w for g, w in table.items()] or [0.0]))
        return self.np.array(out, dtype=self.np.float32)

    def pool(self, detail):
        if detail not in self.pools:
            path = os.path.join(self.emb_dir, detail + ".json")
            if not os.path.isfile(path):
                self.pools[detail] = None
                return None
            table = json.load(io.open(path, encoding="utf-8"))
            side, cat = {}, {}
            extras = [(os.path.join(MOD_128, "data", detail + ".json"), "", "extra")]
            extras += [(os.path.join(MOD_128, "data", c["id"], detail + ".json"),
                        c["id"] if c["default"] == "separate" else "", c["id"]) for c in self.cats if c["default"] != "off"]
            for path, s, cid in extras:
                if not os.path.isfile(path):
                    continue
                blob = json.load(io.open(path, encoding="utf-8"))
                if "index" in blob:
                    blob = {k: blob["vectors"][i] for k, i in blob["index"].items()}
                for k, v in blob.items():
                    if k not in table:
                        table[k] = v
                        cat[k] = cid
                        if s:
                            side[k] = s
            keys = list(table)
            matrix = self.np.array([table[k] for k in keys], dtype=self.np.float32)
            matrix /= self.np.linalg.norm(matrix, axis=1, keepdims=True)
            self.pools[detail] = (keys, matrix, [side.get(k, "") for k in keys], cat)
        return self.pools[detail]

    def rank(self, detail, q):
        """辞書1つの中の候補（本体の `rank` と同じ）。辞書が無ければ None。"""
        pool = self.pool(detail)
        if pool is None:
            return None
        keys, matrix, sides, cat = pool
        np = self.np
        raw = matrix @ q
        bonus = self.bonus([cat.get(k, "stock") for k in keys])
        sims = raw if bonus is None else raw + bonus
        ranked = list(np.argsort(-sims))
        top = float(sims[ranked[0]])
        side = sides[ranked[0]]
        if side:
            plain = next((i for i in ranked if sides[i] == ""), None)
            if float(raw[ranked[0]]) < self.min_sim or (plain is not None and top - float(sims[plain]) < self.margin):
                side = ""
        order = [i for i in ranked if sides[i] == side][:self.top_k]
        best = float(sims[order[0]])
        # 本体と同じく、候補の幅は足す前の値で決める
        qualified = [i for i in order if float(sims[i]) >= best - (1.0 - self.floor) * float(raw[order[0]])]
        return {"keys": keys, "cat": cat, "raw": raw, "top": order[0], "top_sim": top, "qualified": qualified}

    def pick(self, detail, text):
        np = self.np
        q = self.model.embed(text)
        q = q / np.linalg.norm(q)
        own = self.rank(detail, q)
        if own is None:
            return None
        # 本体と同じく、自分の部位の最良が低いときだけ、ほかの 32x32 の部位も見る（self.cross で切れる）
        slot, pick = detail, own
        own_keys = own["keys"]
        own_sim = float(own["raw"][min(own["qualified"], key=lambda i: self.usage[(detail, own_keys[i])])])
        if self.cross and detail in self.cross_slots and own["top_sim"] > 0 and own_sim < self.cross_below:
            best = None
            for other in self.cross_slots:
                if other == detail:
                    continue
                cand = self.rank(other, q)
                if cand is not None and cand["top_sim"] > 0:
                    sim = float(cand["raw"][cand["top"]])
                    if best is None or sim > best[0]:
                        best = (sim, other, cand)
            if best is not None and best[0] >= max(self.cross_min, own_sim + self.cross_margin):
                slot, pick = best[1], best[2]
        keys = pick["keys"]
        names = keys if slot == detail else ["{}{}.{}".format(self.cross_prefix, slot, k) for k in keys]
        chosen = min(pick["qualified"], key=lambda i: self.usage[(detail, names[i])])
        self.usage[(detail, names[chosen])] += 1
        return {"key": names[chosen], "category": pick["cat"].get(keys[chosen], "stock"),
                "sim": round(float(pick["raw"][chosen]), 3), "top_sim": round(pick["top_sim"], 3),
                "slot": slot, "own_sim": round(own_sim, 3)}

    def image_path(self, detail, key, category):
        if key.startswith(self.cross_prefix):
            # 部位をまたいだ絵は元の部位の絵（本体は頼まれた部位のフォルダに写しを置く）
            detail, _, key = key[len(self.cross_prefix):].partition(".")
        if category in ("stock", "extra"):
            return os.path.join(self.img_dir, detail, key + ".png")
        return os.path.join(MOD_128, "images", category, "Assets", "images", "item_candidates_dark", detail, key + ".png")


# ------------------------------------------------------------------ 一覧の画像
def contact_sheet(rows, picker_paths, out_path):
    from PIL import Image, ImageDraw, ImageFont
    try:
        font = ImageFont.truetype("C:/Windows/Fonts/meiryo.ttc", 13)
    except OSError:
        font = ImageFont.load_default()
    cell_w, cell_h, cols = 300, 76, 6
    groups = collections.OrderedDict()
    for row in rows:
        groups.setdefault(row["genre"], []).append(row)
    height = sum(26 + ((len(v) + cols - 1) // cols) * cell_h for v in groups.values())
    sheet = Image.new("RGB", (cols * cell_w, max(height, 10)), (28, 28, 30))
    draw = ImageDraw.Draw(sheet)
    y = 0
    for genre, items in groups.items():
        label = next((g["label"] for g in GENRES if g["id"] == genre), genre)
        draw.text((6, y + 4), label, fill=(255, 220, 120), font=font)
        y += 26
        for i, row in enumerate(items):
            x, yy = (i % cols) * cell_w, y + (i // cols) * cell_h
            path = picker_paths(row)
            if path and os.path.isfile(path):
                icon = Image.open(path).convert("RGBA")
                scale = min(64 / icon.width, 64 / icon.height)
                icon = icon.resize((max(1, int(icon.width * scale)), max(1, int(icon.height * scale))), Image.NEAREST)
                sheet.paste(icon, (x + 4 + (64 - icon.width) // 2, yy + 4 + (64 - icon.height) // 2), icon)
            color = (120, 200, 255) if row["category"] not in ("stock", "extra") else (200, 200, 200)
            draw.text((x + 72, yy + 2), (row["name"] or "")[:16], fill=(255, 255, 255), font=font)
            draw.text((x + 72, yy + 19), "{} {}".format(row["detail"], row["category"])[:34], fill=color, font=font)
            ap = row["appearance"] or ""
            draw.text((x + 72, yy + 36), ap[:34], fill=(255, 150, 150) if row["jp"] else (180, 180, 180), font=font)
            draw.text((x + 72, yy + 53), ap[34:68], fill=(255, 150, 150) if row["jp"] else (180, 180, 180), font=font)
        y += ((len(items) + cols - 1) // cols) * cell_h
    sheet.save(out_path)


def summarize(rows):
    by = collections.defaultdict(lambda: [0, 0])
    for row in rows:
        by[row["source"]][0] += 1
        by[row["source"]][1] += row["jp"]
    for source, (n, jp) in sorted(by.items()):
        print("  {:8s} 品 {:3d}  日本語の外見文 {:3d}".format(source, n, jp))
    cats = collections.Counter()
    for row in rows:
        cats[(row["genre"], "足した絵" if row["category"] not in ("stock", "extra") else "素の絵")] += 1
    for genre in [g["id"] for g in GENRES]:
        added, stock = cats[(genre, "足した絵")], cats[(genre, "素の絵")]
        if added or stock:
            print("  {:9s} 足した絵 {:3d} / 素の絵 {:3d}".format(genre, added, stock))


# ------------------------------------------------------------------ 本体
def main():
    ap = argparse.ArgumentParser(description="品の外見文と 128_ の絵の選ばれ方を LLM で確かめる。")
    ap.add_argument("--backend", choices=("claude", "local"), default="claude")
    ap.add_argument("--sources", default="shop,quest,restock")
    ap.add_argument("--genres", default=",".join(g["id"] for g in GENRES))
    ap.add_argument("--base-url")
    ap.add_argument("--model-pattern")
    ap.add_argument("--port", type=int, default=51991)
    ap.add_argument("--game-dir")
    ap.add_argument("--tag", default="")
    ap.add_argument("--sheet", help="既存の jsonl から一覧の画像だけ作り直す")
    ap.add_argument("--restock-old", action="store_true", help="312_ 版5までの外見文の指示（日本語）で送る")
    ap.add_argument("--event-note", action="store_true", help="event: 128_ のイベントの外見文の手当てを当てて送る")
    ap.add_argument("--event-runs", type=int, default=3, help="event: 記録1件あたりの回数")
    ap.add_argument("--genre-preset", action="store_true", help="世界ごとにジャンルの重み（GENRE_PRESET）を置いて選ぶ")
    args = ap.parse_args()

    game_dir = find_game_dir(args.game_dir)
    picker = Picker(game_dir)
    paths = lambda row: picker.image_path(row["detail"], row["key"], row["category"]) if row.get("key") else None
    if args.sheet:
        rows = [json.loads(line) for line in io.open(args.sheet, encoding="utf-8")]
        contact_sheet(rows, paths, os.path.splitext(args.sheet)[0] + ".png")
        summarize(rows)
        return 0

    genres = [g for g in GENRES if g["id"] in args.genres.split(",")]
    sources = args.sources.split(",")
    compact = qgp.load_compact_module()
    runaway = qgp.load_runaway_module()   # ゲームと同じく 139_ の件数の上限を付ける（依頼の敵の一覧の暴走）
    requests = []
    if "shop" in sources:
        requests += shop_requests(game_dir, genres)
    if "quest" in sources:
        requests += quest_requests(game_dir, genres)
    if "restock" in sources:
        requests += restock_requests(genres, args.restock_old)
    if "event" in sources:
        requests += event_requests(game_dir, args.event_runs, args.event_note)

    os.makedirs(OUT_DIR, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    base = os.path.join(OUT_DIR, "item_image_probe_{}_{}{}".format(stamp, args.backend, ("_" + args.tag) if args.tag else ""))
    sender = ClaudeSender() if args.backend == "claude" else \
        LocalSender(game_dir, args.port, args.base_url, base + "_server.log", args.model_pattern)
    rows, failures = [], 0
    try:
        with io.open(base + ".jsonl", "w", encoding="utf-8") as out:
            for n, request in enumerate(requests, 1):
                schema = request.get("schema") or embedded_schema(request["messages"], compact)
                if args.backend == "local":
                    schema, _applied = runaway.with_max_items(schema, runaway.limits())
                try:
                    got = sender.generate(request, schema)
                    data = json.loads(got["content"])
                except Exception as exc:
                    failures += 1
                    print("  {:>3}/{} {} {} {}: 失敗 {}".format(n, len(requests), request["genre"], request["source"],
                                                              request["where"], str(exc)[:160]), flush=True)
                    continue
                items = items_of(data, request["source"])
                for name, desc, appearance, kind, sub in items:
                    detail = detail_of(kind, sub)
                    picker.world_genres = GENRE_PRESET.get(request["genre"], {}) if args.genre_preset else {}
                    picked = picker.pick(detail, appearance) or {}
                    row = {"backend": args.backend, "model": got.get("model"), "genre": request["genre"],
                           "source": request["source"], "where": request["where"], "name": name, "description": desc,
                           "appearance": appearance, "jp": bool(JP.search(appearance or "")), "item_type": kind,
                           "sub_type": sub, "detail": detail, "key": picked.get("key"),
                           "category": picked.get("category", "none"), "sim": picked.get("sim"),
                           "top_sim": picked.get("top_sim")}
                    rows.append(row)
                    out.write(json.dumps(row, ensure_ascii=False) + "\n")
                    out.flush()
                print("  {:>3}/{} {} {} {}: {}品 {}秒".format(n, len(requests), request["genre"], request["source"],
                                                          request["where"], len(items), got["seconds"]), flush=True)
    finally:
        if isinstance(sender, LocalSender):
            sender.close()
    contact_sheet(rows, paths, base + ".png")
    print("  記録: {}.jsonl / .png  失敗 {}".format(base, failures))
    if isinstance(sender, ClaudeSender):
        print("  トークン: {}".format(dict(sender.usage)))
    summarize(rows)
    return 0


if __name__ == "__main__":
    sys.exit(main())
