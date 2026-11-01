# -*- coding: utf-8 -*-
"""プレイヤーの行いから土地ごとの評判を編み、会話と情景描写に注入する。

ゲームは既に「その土地で何を成したか」を文章で積んでいる
（`area_history[エリアid]["achievements"]`。GAME.md §2.20）。
手配度も滞在歴も完了した依頼も同じで、**素材は全部ゲームの側に在る**。
足りないのはその素材と会話の間の繋ぎで、この MOD が受け持つのはそこだけ。

```
    素材（achievements / lawfulness / residency / 完了した依頼）
        │
        ├─ 到着・日数の経過で印を照合 ─┬─ 土地の印が変われば ── 評判の編纂（LLM 1回）
        │                             └─ 質的な変化があれば ── 二つ名の編纂（LLM 1回）
        │                                     どちらも別スレッド・直列  │
        │                                   state/reputation/<世界×主人公>.json
        │                                                       │
        └─ 会話5関数 / 情景描写 ── 注入するのは**キャッシュ済みの文字列だけ**
```

会話フックの中で LLM を回さない（MODS.md `317_reputation`）。
編纂は素材が変わったときだけ走るので、会話のたびに遅くならず、
推論が返らなくても会話が止まらない。

## 二つ名は世界に1つ。変わるのは質的な変化のときだけ

評判は土地ごと、二つ名は全土地を合わせて1つ（MODS.md `317_reputation` の「二つ名が編み直される契機」）。
二つ名は噂話が属人化したもので、土地ごとに別の名で呼ばれるより、
1つの名が土地を越えて付いて回る方が像に合う。表示も1箇所で済む。

編み直す契機は質的な変化の3つだけ（`epithet_mark` / `epithet_due`）。
評判の立つ土地の増減・手配の反転・出来事の合計の倍増。
あわせて頼み文にいままでの名を渡し、
「明確に覆すのでなければ同じ名を返す」と縛る（契機で回数を、指示で結果を絞る二段構え）。
入力は各地の**編纂済みの評判文**で、素の出来事は渡さない
（1段目が均した事実をここで並べ直すと、同じことが2度 LLM を通る）。

二つ名が付くのは、名声＋悪名（`fame_summary`）がしきい値に届き、評判の立つ土地が規定の数以上のときだけ。
開始の村の駆除の依頼を数件片付けただけで「土地を問わず」名が広まるのは過剰なので、
件数ではなく行いの重さ（依頼・物語の依頼・闘技場を難易度の2乗）で数える（MODS.md `317_reputation` の「名声と悪名」）。
名声は記録から毎回数え直す。悪名だけはゲームに経過が無いので控えに積む（`accumulate_notoriety`）。
1つの土地で名声＋悪名が線を越えると、その土地の評判の編纂で呼び名も頼む。
名声・悪名・呼び名・各地の評判は、MOD 用のボタンの一覧から開く評判の画面（`window`）で見せる。

## 注入するのは「知っている」であって「好き」ではない

`125_balance_charisma_impression` が直したのは
「魅力が高いと初対面から全員に好かれる」だった。
評判で同じ轍を踏まないよう、注入する文には**好悪の方向を書かない**。
`affinity` にも `affinity_text` にも触らない（GAME.md §2.25.1）。
NPC がどう感じるかは NPC の側（性格・手配度との関係）が決める。
そのために注入の末尾に1行だけ、噂と感情を切り分ける但し書きを添える。

## 注入の位置は NPC の複製の `profile`

最初の案では `conversation_starter` の messages 差し替えと
`retrieved_knowledge` への追加を挙げていたが、実装では `311_` と同じ
**第4引数の NPC を浅く複製して `profile` に足す**形を採った。理由は2つ。

* 会話5関数は先頭4引数の並びが共通なので、1箇所で5経路すべてに届く。
  `retrieved_knowledge` の位置は関数ごとに違う（`conversation_facilitator` は12番目、
  `..._after_retrieval` は11番目、`..._in_quest` には無い）ので、
  そちらに乗せると経路ごとに位置を数えることになる
* messages を差し替える形は「第一声だけ」に効く。
  評判は会話の途中で尋ねられても効いていてほしい

ゲーム世界の NPC 本体も会話履歴も触らない。触ると別スレッドとセーブに漏れる。

## 分かったこと: ゲームは既に `area_achievements` を会話に渡している

`out/recon/targets.txt` の並びで、会話5関数のうち3つが素材を直に受け取っている:

    conversation_starter(..., area_residency, area_achievements)
    conversation_facilitator(..., area_residency, area_achievements, ...)
    conversation_facilitator_after_retrieval(..., area_residency, area_achievements, ...)

つまり素の achievements は既にプロンプトに載っている見込みが高い。
この MOD が足すのは**素材の言い換えではなく、編纂の結果**
（手配・依頼・滞在まで込みの1つの評判と二つ名）で、
重なりを増やさないために編纂の頼み文で「出来事を並べ直さない」と縛ってある。
実機で `area_achievements` に何が入っているかは `LOG_MATERIAL` が1回だけ記録する。

## セーブには書かない

キャッシュも二つ名も `state/reputation/<世界×主人公>.json` のみ（TECH.md §3.11）。
`area_history` は**読むだけ**で、`lawfulness` を書き換えない
（手配を動かすのは `309_` と `316_` の仕事）。

## 控えは周回ごと

評判も二つ名も主人公の行いから編んだもので、セーブと同じ寿命を持つ。
世界名だけの鍵だと、同じ世界で主人公を作り直したとき、
前の主人公の評判と二つ名が新しい主人公に付いて回る。
そこで鍵は周回の鍵（世界×主人公）で引く（`WorldStore.playthrough`。TECH.md §5.4）。
世界名だけのファイルが残っていれば、そのとき遊んでいる主人公のものとして移す
（その世界に別の主人公の周回が在れば移さない。ローダの `adopt`）。
引き直しの頼み（`121_` が書く `.reroll.json`）も同じ鍵で読む。
この MOD は `World.__init__` を包まないので、鍵は `app` から引いてよい。
"""

import datetime
import os
import random
import sys
import time

from instantale_modloader import (arrivals, durations, frames, hudicons, jobs, llm, loads, modnpc,
                                  ui, wanted)
from instantale_modloader.state import WorldStore, world_filename

from . import material, window

# ---- 設定（既定値は mod.json の "settings" と一致させること。
#      `tools/check_mods.py` が AST で突き合わせる）------------------------
USE_EPITHET = True        # 二つ名を編纂し、注入にも載せるか
EPITHET_CHARS = 8         # 二つ名の上限（全角）。既定8は実機の所感（MODS.md `317_reputation` の「二つ名が編み直される契機」）
EPITHET_RANDOM_LENGTH = True  # 編纂のたびに狙う長さを揺らすか（下限3〜上限）
EPITHET_FAME = 100        # 二つ名が付く名声＋悪名（MODS.md `317_reputation` の「名声と悪名」）
EPITHET_MIN_LANDS = 2     # 二つ名が付くのに要る、評判の立つ土地の数
USE_LAND_NAMES = True     # 土地ごとの呼び名を付けるか
LAND_NAME_FAME = 100      # その土地の名声＋悪名がこれ以上で呼び名が付く
IN_CONVERSATION = True    # 会話5関数に注入するか
#: 会話の相手の `profile` に評判を足す順（ローダの `modnpc` の notes。小さいほど先）。
#: 自分で包んでいたときの重なり（外側から 321・317・403・311・326・301）を保つ。TECH.md §3.3.21。
NOTES_PRIORITY = -5
IN_NARRATION = False      # 情景描写（narrator）に注入するか
MIN_DEEDS = 2             # 評判が立つ最低件数（achievements ＋ 完了した依頼）
INJECT_DETAIL = "brief"   # 注入の長さ。brief=1文 / detailed=3文まで
LOG_MATERIAL = True       # 素材の実行時の形を1回だけログに残すか

# ---- 以下は設定にしない値 -------------------------------------------------

LOG_BASENAME = "reputation.log"

# グローバルの `random` から引くとゲーム自身の乱数列がずれる（TECH.md §6.1）。
_RNG = random.Random()

#: 周回（世界×主人公）ごとのキャッシュを置くフォルダ（`state/` の下）。
#: ファイル名は `instantale_modloader.state.world_filename` が作る。
#: 規則をここに写さない（写した版がずれた実例が TECH.md §3.2.3 に在る）。
STATE_DIRNAME = "reputation"

#: 自前の `manager_name`。
#: 付けると編纂の頼み文と返答が
#: `output_data/<世界>/<PC>/mod_reputation_compile/N.json` に残り、質を後から見られる。
MANAGER_COMPILE = "mod_reputation_compile"

#: 二つ名の編纂（2段目）の `manager_name`。1段目と分けて別々に残す。
MANAGER_EPITHET = "mod_reputation_epithet"

#: 編纂の制限時間（秒）。**キーワードで必ず渡す**（TECH.md §5.3）。
#: ゲーム側の既定は無期限で、1回返らないと編纂のワーカーが以後ずっと止まる。
COMPILE_TIMEOUT = 120

#: 待ち行列に積んだままにする仕事の上限。溢れたら古い方を捨てる。
#: 照合は評判の立つ土地をすべて見るので（版6）、遊び込んだ世界を初めて読み込んだ回は土地の数だけ積まれる。
#: 同じ土地は二度積まない（`job_key`）ので、土地の数より大きければ捨てることはない。
MAX_PENDING = 64

#: 素材の照合をこの秒数より短い間隔では行わない。
#: 施設への移動は頻繁に起きるので、そのたびに全依頼を走査しないための間引き。
CHECK_INTERVAL = 5.0

#: 注入の長さ（MODS.md `317_reputation` の「素材とプロンプトの重なり」）。
#: プロンプトは毎回全文載る世界で、`311_` 併用時は会話開始時点で 2,206字の実測がある。
#: 文の数と字数の両方で頭打ちにするのは、1文に何でも詰める返答があるため。
DETAIL_SENTENCES = {"brief": 1, "detailed": 3}
DETAIL_CHARS = {"brief": 120, "detailed": 300}

#: 狙う長さの下限（`EPITHET_RANDOM_LENGTH` が引く乱数の底）。
#: 1〜2字の名は据わりが悪いので3字から。設定にはしない
#: （上限とランダムの有無だけで長さの遊びは足りる。増やすと GUI が説明で埋まる）。
#: 上限そのもの（`EPITHET_CHARS`）は設定に出してある。
#: 超えた名を切り詰めずに捨てる規則（`clean_epithet`）は変わらない。
EPITHET_LENGTH_FLOOR = 3

#: 全土地の出来事の合計がこの倍率を超えたら二つ名を編み直す
#: （質的な変化の3つ目。MODS.md `317_reputation` の「二つ名が編み直される契機」）。倍々なので終盤ほど動かなくなる。
EPITHET_DOUBLE = 2

#: 土地の控え1件の鍵と、ファイルに書くときの並び。
#: 並びを固定するのは、後から土地ごとに見比べるため（`311_` と同じ）。
#: 旧版は土地ごとに "epithet" を持っていた。残っていても知らない鍵として
#: 後ろへ回るだけで、読み側はもう見ない（`ordered_record`）。
RECORD_KEYS = ("area", "name", "day", "updated", "reputation", "nickname", "fingerprint")

#: 二つ名の控え（世界に1つ）の鍵と並び。
#: `description` はその名が何を指すかの説明。**ゲームには出さない**内部データで、
#: 後から控えを読んだときに名の由来が分かるように編纂と一緒に書かせる。
#: `mark` は編纂したときの質的状態（`epithet_mark`）。次の契機の比較相手。
EPITHET_RECORD_KEYS = ("epithet", "description", "day", "updated", "mark")

#: 名声の控え（世界に1つ）の鍵と並び。`121_` の人物欄が読む（MOD をまたいだ取り決め）。
#: しきい値と土地の数も一緒に書くのは、読む側にこちらの設定を持たせないため。
#: `areas` は名声か悪名が 0 でない土地と評判の立つ土地で、中身は `{"fame": 名声, "notoriety": 悪名, "standing": 評判の立つ土地か}`。
FAME_RECORD_KEYS = ("total", "notoriety", "threshold", "lands", "min_lands", "eligible",
                    "land_threshold", "areas")

#: 依頼1件の名声は「難易度の2乗 ÷ これ」。難易度 25 の依頼が 25 になる。
#: 2乗にするのは、開始の村の依頼（1〜5）を何件重ねても誤差に留め、終盤の大物ほど大きく効かせるため
#: （MODS.md `317_reputation` の「名声と悪名」）。
FAME_DIVISOR = 25.0

#: 物語の依頼の重み。本編の節目は通常の依頼より広く知られる。
STORY_WEIGHT = 2.0

#: 呼び名の上限（全角）。二つ名と同じ上限を使う（`EPITHET_CHARS`）。
LAND_NAME_LINE = "この土地では「{nickname}」と呼ばれている。"
KEY_NICKNAME = "nickname"

#: 評判の画面の入口（画面左下の MOD 用のボタンの一覧の行。TECH.md §3.3.15）。
#: 絵は `tools\draw_hud_icons.py` が描き出す（月桂冠）。並びは 338（10）・339（20）・家と施設（30）の後。
ICON_KEY = "reputation"
ICON_FILES = ("icon_laurel.png", "icon_laurel_hover.png", "icon_laurel_click.png")
ICON_ORDER = 40

#: 二つ名の引き直しの頼み（`121_` の人物欄のボタンが書くファイル）。
#: 在れば消して、いまの名を除いて編み直す。中身は読まない。
#: 名前は `121_` の `sheet.REROLL_SUFFIX` と同じでなければならない
#: （MOD をまたいだ取り決め。MODS.md `317_reputation` の「控えの形」）。
REROLL_SUFFIX = ".reroll.json"

#: 注入する文の組み立て。
HEADING = "【{area}での{player}の評判】"
EPITHET_LINE = "土地を問わず「{epithet}」の二つ名で知られている。"
GUIDE = ("これはこの土地に広まっている噂で、あなたも耳にしている。"
         "{player}への好悪はあなた自身の性格が決める。")

#: 土地の名前が引けなかったときの言い方。
AREA_FALLBACK = "この土地"

#: 編纂の返答から読む鍵。
KEY_REPUTATION = "reputation"
KEY_EPITHET = "epithet"
KEY_DESCRIPTION = "description"

#: 二つ名の説明の上限。ゲームには出さない内部の覚え書き（控えを読むときの文脈）
#: なので緩く、超えたら（名と違って）捨てずに切り詰める。欠けても害が無い。
EPITHET_DESC_CHARS = 120

#: ワーカーとキャッシュの置き場所。
#: **`apply()` の中で作ってはいけない**（TECH.md §3.4）。
#: `apply()` は再注入と遅延当て直しで何度も走り、そのたびに入れ物が作り直される。
#: 前の世代のワーカーが生きている間に2本目が起動すると、
#: 錠が別インスタンスになり、同じ `state/reputation/<世界×主人公>.json` を
#: 排他なしで read-modify-write できてしまう。
#: `sys` に置けば世代をまたいで同じ1組を共有できる（`311_` / `118_` と同じ手）。
STATE_STORE_ATTR = "__instantale_reputation_store__"


# --------------------------------------------------------------------- 素の関数
# `apply()` の外に出してあるものは、ゲームも `ctx` も要らない部分。
# `tools/tests/test_wip_reputation.py` がここを直接呼ぶ。

def sentence_limit():
    return DETAIL_SENTENCES.get(INJECT_DETAIL, DETAIL_SENTENCES["brief"])


def char_limit():
    return DETAIL_CHARS.get(INJECT_DETAIL, DETAIL_CHARS["brief"])


def deeds_count(item):
    """評判の素になる出来事の件数。成した事と片付けた依頼の合計。

    依頼を数に入れるのは、どちらも「その土地でやったこと」だから。
    最初の案では achievements だけを数える案だったが、
    依頼を片付けただけの土地で評判が立たないのは素材の取りこぼしになる。
    物語の依頼と闘技場の勝ちも同じ理由で数える（版6）。
    """
    if not isinstance(item, dict):
        return 0
    arena = item.get("arena") if isinstance(item.get("arena"), dict) else {}
    return (len(item.get("achievements") or []) + len(item.get("quests") or [])
            + len(item.get("story") or []) + (arena.get("wins") or 0))


def is_wanted(item):
    """手配されているか。**0 未満で犯罪者**（GAME.md §2.20 の実測）。

    平常値 10 からどれだけ下がったかではなく、ゲーム自身が引いている線を使う。
    独自の閾値を持つと `316_`（追手）と食い違い、
    「追手は来るのに誰も噂していない」土地ができる。
    """
    if not isinstance(item, dict):
        return False
    value = item.get("lawfulness")
    return isinstance(value, int) and value < 0


def qualifies(item):
    """評判が立つか。立たない土地には**何も注入しない**（MODS.md `317_reputation`）。

    手配されていれば件数を問わない。
    悪名は1回で立つし、その土地の achievements が空でも
    「危険な者が居る」はもう噂になっている。
    """
    return deeds_count(item) >= MIN_DEEDS or is_wanted(item)


# --------------------------------------------------------------- 名声と悪名
def deed_fame(difficulty):
    """依頼1件・闘技場の1勝の名声。難易度（相手の格）の2乗 ÷ `FAME_DIVISOR`。"""
    if isinstance(difficulty, bool) or not isinstance(difficulty, (int, float)) or difficulty <= 0:
        return 0.0
    return difficulty * difficulty / FAME_DIVISOR


def _median(values):
    values = sorted(values)
    if not values:
        return 0
    middle = len(values) // 2
    return values[middle] if len(values) % 2 else (values[middle - 1] + values[middle]) / 2.0


def area_fame(entry):
    """土地1つの名声。`material.survey` の1件から数える（MODS.md `317_reputation` の「名声と悪名」）。

    足すのは4つで、どれも `deed_fame` の目盛り。
      完了した依頼        難易度ごとに1件ずつ
      依頼の付かない成した事  依頼より多い分だけ、その土地の依頼の難易度の中央値で
      物語の依頼          `STORY_WEIGHT` 倍
      闘技場の勝ち        破った相手の格ごとに1勝ずつ（闘技場の相手の格は依頼の難易度と同じ目盛り）
    成した事は依頼1件につき1文ずつ書かれる（GAME.md §2.20）ので、件数をそのまま足すと二重に数える。
    悪名はここに入れない（`accumulate_notoriety`）。
    """
    if not isinstance(entry, dict):
        return 0
    fame = sum(deed_fame(value) for value in entry.get("done_difficulties") or ())
    extra = max(0, (entry.get("achievements") or 0) - (entry.get("done") or 0))
    if extra:
        fame += extra * deed_fame(_median(entry.get("difficulties") or ()))
    fame += STORY_WEIGHT * sum(deed_fame(value) for value in entry.get("story_difficulties") or ())
    fame += sum(deed_fame(value) for value in entry.get("arena_ranks") or ())
    return int(round(fame))


def accumulate_notoriety(ledger, stats):
    """土地ごとの悪名を積んだ写しと、変わったかを返す。`(ledger, changed)`。

    悪名は手配の重さ（`wanted.weight_of`。`309_` / `316_` と同じ物差し）が**増えた分の累計**。
    時効・罰金・恩赦で重さが減っても引かない。減った後にまた増えれば、増えた分を足す。
    ゲームには手配の経過の記録が無いので、照合のたびに前の重さ（`last`）と比べる。
    初めて見た土地は、そのときの重さから数え始める。

        {エリアid: {"total": 悪名, "last": 前に見た重さ}}
    """
    out = {}
    for area_id, record in (ledger or {}).items():
        if isinstance(record, dict):
            out[str(area_id)] = {"total": int(record.get("total") or 0),
                                 "last": int(record.get("last") or 0)}
    changed = False
    for area_id, item in (stats or {}).items():
        weight = wanted.weight_of(item.get("lawfulness") if isinstance(item, dict) else None)
        record = out.get(area_id)
        if record is None:
            if weight <= 0:
                continue
            record = out[area_id] = {"total": 0, "last": 0}
        if weight > record["last"]:
            record["total"] += weight - record["last"]
            changed = True
        if weight != record["last"]:
            record["last"] = weight
            changed = True
    ordered = {key: out[key] for key in sorted(out, key=ui.id_sort_key)}
    return ordered, changed


def fame_summary(stats, notoriety=None):
    """全土地の名声と悪名の控え（`FAME_RECORD_KEYS` の並び）。

    二つ名が付くのは名声＋悪名がしきい値に届き、かつ評判の立つ土地が規定の数以上のとき。
    「土地を問わず知られている」と書く以上、1つの土地の中だけの評判では付けない。
    悪名を足すのは、悪事だけで名を広めた者にも、英雄と悪党が混じった者にも名が付くように。
    """
    if not isinstance(stats, dict):
        stats = {}
    notoriety = notoriety if isinstance(notoriety, dict) else {}
    areas = {}
    for area_id in sorted(set(stats) | set(notoriety), key=ui.id_sort_key):
        item = stats.get(area_id) if isinstance(stats.get(area_id), dict) else {}
        fame = area_fame(item)
        bad = int((notoriety.get(area_id) or {}).get("total") or 0)
        # 評判の立つ土地か。評判の画面が「まだ編んでいない」と「まだ立たない」を言い分けるのに使う
        standing = item.get("deeds", 0) >= MIN_DEEDS or bool(item.get("wanted"))
        if fame or bad or standing:
            areas[area_id] = {"fame": fame, "notoriety": bad, "standing": standing}
    total = sum(item["fame"] for item in areas.values())
    bad_total = sum(item["notoriety"] for item in areas.values())
    lands = sum(1 for item in areas.values() if item["standing"])
    return {"total": total, "notoriety": bad_total, "threshold": EPITHET_FAME,
            "lands": lands, "min_lands": EPITHET_MIN_LANDS,
            "eligible": total + bad_total >= EPITHET_FAME and lands >= EPITHET_MIN_LANDS,
            "land_threshold": LAND_NAME_FAME, "areas": areas}


def land_named(fame, area_id):
    """その土地に呼び名が付く線を越えているか。`fame` は `fame_summary` の控え。"""
    if not USE_LAND_NAMES or not isinstance(fame, dict):
        return False
    item = (fame.get("areas") or {}).get(str(area_id))
    if not isinstance(item, dict):
        return False
    return (item.get("fame") or 0) + (item.get("notoriety") or 0) >= LAND_NAME_FAME


def fame_shortfall(fame):
    """二つ名がまだ付かない理由（ログ用）。"""
    return "名声 {}＋悪名 {}/{}、評判の立つ土地 {}/{}".format(
        fame.get("total"), fame.get("notoriety", 0), fame.get("threshold"),
        fame.get("lands"), fame.get("min_lands"))


def mark_eligible(mark):
    """控えた印が「二つ名が付く状態」で取られたか。印の `fame` は名声＋悪名。

    版5までの印には名声が無い。そのときは土地の数だけで見る
    （1つの土地の中で付いた名を、条件に届いたときに引き継がないため）。
    """
    if not isinstance(mark, dict):
        return False
    lands = len(mark.get("qualifying") or ())
    fame = mark.get("fame")
    if not isinstance(fame, int) or isinstance(fame, bool):
        return lands >= EPITHET_MIN_LANDS
    return fame >= EPITHET_FAME and lands >= EPITHET_MIN_LANDS


def trim_sentences(text, limit):
    """文の数で切る。句点で数え、最後の句点までを返す。

    句点が無い返答（箇条書き・改行だけで区切る書き方）は切らずに字数側へ任せる。
    ここで無理に切ると語の途中で終わる。
    """
    if limit <= 0:
        return ""
    parts = [part for part in text.replace("\n", "").split("。") if part.strip()]
    if len(parts) <= limit:
        return text.strip()
    return "。".join(parts[:limit]) + "。"


def clean_reputation(text):
    """編纂された評判文を注入できる形に均す。読めなければ空文字。"""
    if not isinstance(text, str):
        return ""
    body = " ".join(text.split())
    if not body:
        return ""
    return frames.short(trim_sentences(body, sentence_limit()), char_limit())


def pick_epithet_length():
    """今回の編纂で狙う長さ（全角）。

    ランダムを切っていれば上限そのもの。
    揺らすのは**頼み文の狙いだけ**で、上限は毎回同じ（`clean_epithet` が守る）。
    据え置きの指示（同じ名を返す）がある回は、狙いより据え置きが勝つ。
    それでよい ― 揺らしたいのは新しく名を作る回の語感で、名の安定は崩さない。
    """
    if not EPITHET_RANDOM_LENGTH:
        return EPITHET_CHARS
    if EPITHET_CHARS <= EPITHET_LENGTH_FLOOR:
        return EPITHET_CHARS
    return _RNG.randint(EPITHET_LENGTH_FLOOR, EPITHET_CHARS)


def clean_epithet(text):
    """二つ名を均す。鉤括弧で囲んで返ってくることがあるので外す。

    上限（`EPITHET_CHARS`）を超える名は**切り詰めずに空へ倒す**。
    「灰の街の…」のような欠けた名を注入・表示するくらいなら、無い方がよい。
    """
    if not isinstance(text, str):
        return ""
    body = " ".join(text.split()).strip("「」\"'　 ")
    if not body or body in ("なし", "無し", "特になし", "不明", "none", "None"):
        return ""
    return body if len(body) <= EPITHET_CHARS else ""


def echoes_material(epithet, player, item):
    """二つ名が素材の写しになっているか。写しは二つ名として受けない。

    gemma-4 26B の実測（VERIFICATION_LOG.md §2.63）で出た2つの逃げ方への番人。
    本人の名前（「旅人リン」「旅人」）と、依頼の題名そのまま
    （「沖仲仕の代役」、「の」を抜いただけの「沖仲仕代役」も同じ）。
    頼み文でも禁じたが、頼み文だけでは守らないモデルがあるので両側で持つ。

    「橋の修復者」のような**派生形は写しとしない**
    （題名と一致するのは正規化して同じになるものだけ）。
    名前も同じで、「暴走のリン」のように**語が足された形は写しとしない**
    （頼み文で禁じたのは「そのまま」。プレイヤー名を「リン」にした実測で
    この形が実際に出た）。名前を除いた残りが2字に満たなければ写し。
    """
    if not isinstance(epithet, str) or not epithet:
        return False

    def norm(text):
        return "".join(text.split()).replace("の", "")

    if isinstance(player, str) and player:
        if epithet in player:
            return True
        if player in epithet and len(norm(epithet.replace(player, ""))) < 2:
            return True
    sources = []
    if isinstance(item, dict):
        sources = list(item.get("achievements") or []) + list(item.get("quests") or [])
    return any(isinstance(source, str) and norm(epithet) == norm(source)
               for source in sources)


def clean_epithet_description(text):
    """二つ名の説明を均す。読めなければ空文字。"""
    if not isinstance(text, str):
        return ""
    return frames.short(" ".join(text.split()), EPITHET_DESC_CHARS)


def looks_like_json_attempt(body):
    """JSON を書こうとして失敗した返答か。

    そうであれば素の文章の受け皿へ落とさない。
    落とすと壊れた JSON がそのまま評判文になり、
    以後の会話に「{ 」から始まる噂が注入される（`311_` が同じ受け皿を持っている）。
    """
    return body.startswith("{") or body.startswith("```") or any(
        '"' + key + '"' in body or "'" + key + "'" in body
        for key in (KEY_REPUTATION, KEY_EPITHET, KEY_DESCRIPTION, KEY_NICKNAME))


def parse_result(raw):
    """評判の編纂（1段目）の返答を `{"reputation": str}` に直す。読めなければ `None`。

    ゲームの構造化出力の経路を使わない理由は `311_` と同じ
    （引数の形を間違えると内部スレッドで例外が上がり、**呼び出しが永久に返らない**。
    GAME.md §2.12）。
    加えてローカルのモデルは `json_schema` を黙って守らないことがあるので、
    どちらにせよ返答を自分で読む必要がある。

    前置きや囲みが付くことも、JSON にならず素の文章で返ることもある。
    素の文章はそのまま評判文として受ける（二つ名は諦める）。
    捏造ではなく体裁の崩れなので、捨てるより拾う方が得。
    """
    if isinstance(raw, dict):
        data = raw
    elif isinstance(raw, str):
        # 囲みを剥がして JSON を1つ取り出すところはローダの語彙（`llm`）。
        # 素の文章の受け皿がここに在るので、剥がした本文も手元に要る。
        body = llm.strip_fence(raw)
        data = llm.parse_json(body)
        if data is None:
            if looks_like_json_attempt(body):
                return None
            text = clean_reputation(body)
            return {KEY_REPUTATION: text} if text else None
    else:
        return None
    reputation = clean_reputation(data.get(KEY_REPUTATION))
    if not reputation:
        return None
    result = {KEY_REPUTATION: reputation}
    if KEY_NICKNAME in data:
        # 呼び名は二つ名と同じ均し方（上限超えは切り詰めずに空）。空は「付けなかった」
        result[KEY_NICKNAME] = clean_epithet(data.get(KEY_NICKNAME))
    return result


def as_bucket(data):
    """読んだものを `{"areas": 土地別, "epithet": 世界に1つ, "fame": 世界に1つ}` に均す。

    旧版のファイル（土地の控えに "epithet" が混ざっている）もこの形に収まる
    （読み側がもう見ないだけで、鍵は `ordered_record` が後ろへ回して残す）。
    版5までのファイルには "fame" が無い。空のまま読み、次の照合で書く。
    形を均すだけで書き戻しはしないので、2つ目は常に False。
    """
    if not isinstance(data, dict):
        data = {}
    out = {}
    for key in BUCKET_KEYS:
        value = data.get(key)
        out[key] = value if isinstance(value, dict) else {}
    return out, False


#: 控えの欄の並び。`fame` は照合のたびに数え直す写し、`notoriety` は積んだ悪名（数え直せない）。
BUCKET_KEYS = ("areas", "epithet", "fame", "notoriety")


def stored_bucket(bucket):
    """書く形。まだ中身の無い欄は作らない（土地の欄だけは空でも書く）。"""
    data = {"areas": bucket["areas"]}
    for key in BUCKET_KEYS[1:]:
        if bucket.get(key):
            data[key] = bucket[key]
    return data


def ordered_record(record):
    """キャッシュ1件を `RECORD_KEYS` の並びに直す。知らない鍵は落とさず後ろへ回す。"""
    if not isinstance(record, dict):
        return {}
    out = {key: record[key] for key in RECORD_KEYS if key in record}
    for key, value in record.items():
        if key not in out:
            out[key] = value
    return out


def reputation_block(record, player, area, epithet="", nickname=""):
    """注入する本文。控えが無ければ空文字（＝何も足さない）。

    二つ名は世界に1つだが、**載せるのは評判の立つ土地の中だけ**。
    評判の無い土地で二つ名だけを注入すると、
    「何も無ければ何も足さない」（MODS.md `317_reputation` の「素材とプロンプトの重なり」）が崩れる。
    呼び名はその土地のものだけを載せる。
    """
    if not isinstance(record, dict):
        return ""
    body = clean_reputation(record.get(KEY_REPUTATION))
    if not body:
        return ""
    lines = [HEADING.format(area=area or AREA_FALLBACK, player=player), body]
    nickname = clean_epithet(nickname) if USE_LAND_NAMES else ""
    if nickname:
        lines.append(LAND_NAME_LINE.format(nickname=nickname))
    epithet = clean_epithet(epithet) if USE_EPITHET else ""
    if epithet:
        lines.append(EPITHET_LINE.format(epithet=epithet))
    lines.append(GUIDE.format(player=player))
    return "\n".join(lines)


def bullet_list(items, empty="（記録なし）"):
    return "\n".join("- " + item for item in items) if items else empty


def lawfulness_line(item):
    """手配の状況を1行で。数値をそのまま見せず、ゲームの線で言い分ける。

    生の `lawfulness` を渡すと、モデルが「10」を高評価と読むことがある
    （小さいほど手配が重い並びは、説明しないと伝わらない）。
    """
    value = item.get("lawfulness") if isinstance(item, dict) else None
    if not isinstance(value, int):
        return "分かっていない"
    if value < 0:
        return "手配されている（重さ {}）".format(-value)
    if value < material.NORMAL_LAWFULNESS:
        return "咎めを受けたことがある（平常より {} 低い）".format(
            material.NORMAL_LAWFULNESS - value)
    return "咎められていない"


def residency_line(item):
    stay = (item.get("residency") if isinstance(item, dict) else None) or {}
    days = stay.get(material.TOTAL_DAYS_KEY)
    if not isinstance(days, int):
        return "分かっていない"
    return "のべ{}日".format(days)


def arena_line(item):
    """闘技場の戦績を1行で。格の目盛りは闘技場の頼み文と同じ言い方で添える（GAME.md の闘技場の節）。"""
    arena = item.get("arena") if isinstance(item, dict) else None
    wins = (arena or {}).get("wins") or 0
    if not wins:
        return "（記録なし）"
    line = "{}で{}勝。最後に破った相手の格は {}".format(
        arena.get("name") or "闘技場", wins, arena.get("top_rank") or "分からない")
    return line + "（格1が雑魚、格70が歴史に名を残す勇者や魔王に値する強者）"


def build_messages(item, player, nickname=None, current_nickname="", exclude_nickname=""):
    """編纂の頼み文。**出来事を並べ直させない**（MODS.md `317_reputation` の「素材とプロンプトの重なり」）。

    ゲーム自身が `area_achievements` を会話に渡している以上、
    ここで素材を要約し直すと同じ事実が言い換えで2度載る。
    頼むのは「土地の人々が何を知っていて、どう呼んでいるか」の1点に絞る。
    `nickname` が真なら、その土地での呼び名も一緒に頼む（呼び名の線を越えた土地だけ）。
    いまの呼び名があれば渡し、覆す材料が無ければ同じ名を返させる（二つ名と同じ据え置き）。
    `exclude_nickname` は評判の画面の再生成。据え置きの代わりに、その名を除いて別の名を頼む。
    """
    area = item.get("area_name") or AREA_FALLBACK
    limit = sentence_limit()
    body = [
        "あなたは「{}」という土地の噂を集める役である。".format(area),
        "以下は {} という人物がこの土地で残した記録である。".format(player),
        "",
        "【この土地で成したこと】",
        bullet_list(item.get("achievements") or []),
        "",
        "【この土地で片付けた依頼】",
        bullet_list(item.get("quests") or []),
    ]
    if item.get("story"):
        body += ["", "【この土地で果たした物語の依頼】", bullet_list(item["story"])]
    if (item.get("arena") or {}).get("wins"):
        body += ["", "【この土地の闘技場での戦績】", arena_line(item)]
    body += [
        "",
        "【この土地での立場】",
        lawfulness_line(item),
        "",
        "【この土地での滞在】",
        residency_line(item),
        "",
        "この記録から、土地の人々のあいだに広まっている評判を書け。",
        "守ること:",
        "- 記録に無いことを足さない",
        "- 出来事を並べ直さない。人々が何を知っているかだけを書く",
        "- 誰かの好意や敵意は書かない。噂の中身だけを書く",
        "- 日本語で{}文以内、{}字以内".format(limit, char_limit()),
    ]
    if nickname:
        current = clean_epithet(current_nickname)
        body += [
            "",
            "あわせて、この土地の人々がこの人物を呼ぶ呼び名を1つ書け。",
            "- 呼び名は評判からの連想で新しく作ってよい",
            "- 本人の名前や「{}」という土地の名前をそのまま使わない".format(area),
            "- 全角{}字以内".format(EPITHET_CHARS),
        ]
        exclude = clean_epithet(exclude_nickname)
        if exclude:
            # 再生成。据え置きの指示と両立しないので、渡すのはどちらか片方だけ（二つ名の引き直しと同じ）
            body.append("- 「{}」という呼び名は使わないことに決めた。別の名を書け".format(exclude))
        elif current:
            body.append("- いまの呼び名は「{}」。新しい記録がこの名を明確に覆すのでなければ、"
                        "同じ名をそのまま返す".format(current))
    body += [
        "",
        "返答は次の形の JSON オブジェクト1個だけとし、他には何も書かない。",
        ('{"reputation": "評判の文", "nickname": "呼び名"}' if nickname
         else '{"reputation": "評判の文"}'),
    ]
    # 二つ名はここでは頼まない。
    # 世界に1つの2段目（`build_epithet_messages`）へ移した（MODS.md `317_reputation` の「二つ名が編み直される契機」）。
    # 写し禁止と空文字への逃げの断り（§6.3 の対策）もそちらが持つ。
    return [{"role": "user", "content": "\n".join(body)}]


# ------------------------------------------------------------ 二つ名（世界に1つ）
def epithet_mark(stats, notoriety=None):
    """全土地の質的状態。二つ名を編み直す契機は**この印の変化だけ**（MODS.md `317_reputation` の「二つ名が編み直される契機」）。

    入れるのは4つ。
      qualifying  評判の立つ土地の一覧（増減どちらも契機。恩赦で外れる側も拾う）
      wanted      手配されている土地の一覧（0 をまたぐ反転を両方向で拾う）
      deeds       全土地の出来事の合計（前回の倍で契機。倍々なので終盤ほど動かない）
      fame        全土地の名声＋悪名（`fame_summary`）。契機ではなく、付くかどうかの線

    滞在日数を入れないのは土地の印（`material.fingerprint`）と同じ理由。
    いくつから評判が立つか（`MIN_DEEDS`）はこちらの方針なので、
    `material.survey` は件数だけを返し、線はここで引く。
    """
    if not isinstance(stats, dict):
        stats = {}
    summary = fame_summary(stats, notoriety)
    qualifying = sorted(
        (aid for aid, item in stats.items()
         if item.get("deeds", 0) >= MIN_DEEDS or item.get("wanted")),
        key=ui.id_sort_key)
    flagged = sorted((aid for aid, item in stats.items() if item.get("wanted")),
                     key=ui.id_sort_key)
    total = sum(item.get("deeds", 0) for item in stats.values())
    return {"qualifying": qualifying, "wanted": flagged, "deeds": total,
            "fame": summary["total"] + summary["notoriety"]}


def epithet_due(record, mark):
    """二つ名を編み直すか。`(立つか, ログに書く理由)` を返す。

    ここが受け持つのは回数を絞る側（契機）。
    結果を絞る側（明確に覆らなければ同じ名を返す）は頼み文が持つ
    （`build_epithet_messages`）。二段構えの理由は MODS.md `317_reputation` の「二つ名が編み直される契機」。
    名声と土地の数が線に届くまでは何もしない。
    届く前に付いた名（版5まで）があれば、届いたときに編み直す。
    """
    if not mark_eligible(mark):
        return False, "名声が足りない"
    old = record.get("mark") if isinstance(record, dict) else None
    if not isinstance(old, dict):
        return True, "初回"
    if not mark_eligible(old):
        return True, "名声が届いた"
    if old.get("qualifying") != mark["qualifying"]:
        return True, "評判の立つ土地が変わった"
    if old.get("wanted") != mark["wanted"]:
        return True, "手配が反転した"
    before = old.get("deeds")
    if (isinstance(before, int) and not isinstance(before, bool) and before > 0
            and mark["deeds"] >= before * EPITHET_DOUBLE):
        return True, "出来事の合計が{}倍になった".format(EPITHET_DOUBLE)
    return False, "質的な変化なし"


def ordered_epithet(record):
    """二つ名の控えを `EPITHET_RECORD_KEYS` の並びに直す。知らない鍵は後ろへ。"""
    if not isinstance(record, dict):
        return {}
    out = {key: record[key] for key in EPITHET_RECORD_KEYS if key in record}
    for key, value in record.items():
        if key not in out:
            out[key] = value
    return out


def build_epithet_messages(player, entries, current, exclude="", length=None):
    """二つ名の編纂（2段目）の頼み文。入力は**各地の編纂済みの評判文**。

    素の出来事を渡さないのは、1段目が既に事実を1文へ均しているから。
    ここに記録まで載せると同じことが2度 LLM を通り、頼み文も土地の数だけ伸びる。

    写し禁止と「連想で新しく作ってよい」の断りは1段目から引き継いだもの
    （無いと 73% が空になる。VERIFICATION_LOG.md §2.63）。
    いままでの名がある回はそれを渡し、据え置きを既定にする。
    """
    lines = [
        "あなたは旅人たちの噂を語り継ぐ語り部である。",
        "以下は {} という人物について、各地に立っている評判である。".format(player),
        "",
        "【各地の評判】",
    ]
    for name, text in entries:
        lines.append("- {}: {}".format(name or AREA_FALLBACK, text))
    lines += [
        "",
        "この人物が土地を越えて呼ばれる二つ名を1つ書け。",
        "守ること:",
        "- 二つ名は評判からの連想で新しく作ってよい",
        "- 本人の名前や土地の名前をそのまま二つ名にしない",
    ]
    if length and length < EPITHET_CHARS:
        # 狙いの長さ（`pick_epithet_length`）。「くらい」で頼み、上限だけ固く言う。
        # 「以内」だけだとモデルは毎回同じ長さ帯に寄る（§6.3 の場面内の収束と同根）。
        lines.append("- 長さは全角{}字くらい。{}字は超えない".format(
            length, EPITHET_CHARS))
    else:
        lines.append("- 全角{}字以内".format(EPITHET_CHARS))
    lines.append("- description には、その名がどの評判から来て何を指すかを"
                 "1〜2文で書く")
    current = clean_epithet(current)
    exclude = clean_epithet(exclude)
    if exclude:
        # 引き直し。据え置きの指示と両立しないので、渡すのはどちらか片方だけ。
        lines.append("- 「{}」という名は使わないことに決めた。".format(exclude)
                     + "別の名を書け")
    elif current:
        lines.append("- いままでの二つ名は「{}」。".format(current)
                     + "新しい評判がこの名を明確に覆すのでなければ、"
                     + "同じ名をそのまま返す")
    lines += [
        "",
        "返答は次の形の JSON オブジェクト1個だけとし、他には何も書かない。",
        '{"epithet": "二つ名", "description": "その名の説明"}',
    ]
    return [{"role": "user", "content": "\n".join(lines)}]


def epithet_result(data):
    """辞書1つを `{"epithet": …, "description": …}` に均す。"""
    return {KEY_EPITHET: clean_epithet(data.get(KEY_EPITHET)),
            KEY_DESCRIPTION: clean_epithet_description(
                data.get(KEY_DESCRIPTION))}


def parse_epithet(raw):
    """二つ名の編纂の返答を `{"epithet": 名, "description": 説明}` に直す。
    読めなければ `None`。

    `None`（読めない）と名が空（読めたが名は無い）を分ける。
    前者は印を控えずに次の照合で引き直し、後者は印を控えて消費と数える
    （分けないと、名を付けないモデルに同じ問いを照合のたびに繰り返すことになる）。

    素の文章の受け皿は評判文と違って**上限内のときだけ**。
    長い前置きは `clean_epithet` が空へ倒すので、その形は「読めない」へ読み替える
    （素の文章に説明は無いので description は空）。
    """
    if isinstance(raw, dict):
        return epithet_result(raw)
    if not isinstance(raw, str):
        return None
    body = llm.strip_fence(raw)
    data = llm.parse_json(body)
    if data is not None:
        return epithet_result(data)
    if looks_like_json_attempt(body):
        return None
    cleaned = clean_epithet(body)
    return {KEY_EPITHET: cleaned, KEY_DESCRIPTION: ""} if cleaned else None


# ------------------------------------------------------------ 評判の画面の中身
def view_model(bucket, area_names, laws, here=""):
    """評判の画面に出す中身（`window.open_window` が描く）。ゲームも Kivy も要らない。

    `bucket` は控え（`as_bucket` の形）、`area_names` は `{エリアid: 名前}`、
    `laws` は `{エリアid: 手配度}`、`here` はいま居る土地の名前。
    土地は名声＋悪名の大きい順。名声も悪名も評判も無い土地は出さない。
    """
    bucket = bucket if isinstance(bucket, dict) else {}
    fame = bucket.get("fame") if isinstance(bucket.get("fame"), dict) else {}
    records = bucket.get("areas") if isinstance(bucket.get("areas"), dict) else {}
    scores = fame.get("areas") if isinstance(fame.get("areas"), dict) else {}
    eligible = bool(fame.get("eligible"))
    epithet = ""
    if USE_EPITHET and eligible:
        epithet = clean_epithet((bucket.get("epithet") or {}).get(KEY_EPITHET, ""))
    rows = []
    for area_id in set(scores) | set(records):
        score = scores.get(area_id) if isinstance(scores.get(area_id), dict) else {}
        record = records.get(area_id) if isinstance(records.get(area_id), dict) else {}
        named = land_named(fame, area_id)
        rows.append({
            "id": str(area_id),
            "name": (area_names or {}).get(str(area_id)) or record.get("name") or str(area_id),
            "fame": int(score.get("fame") or 0),
            "notoriety": int(score.get("notoriety") or 0),
            "lawfulness": (laws or {}).get(str(area_id)),
            "nickname": clean_epithet(record.get(KEY_NICKNAME, "")) if named else "",
            "reputation": clean_reputation(record.get(KEY_REPUTATION)),
            "standing": bool(score.get("standing")),
            # 呼び名の線を越えて、評判も編み終わっている土地（呼び名の再生成のボタンを置く）
            "named": named and bool(record.get(KEY_REPUTATION)),
        })
    rows.sort(key=lambda row: (-(row["fame"] + row["notoriety"]), ui.id_sort_key(row["id"])))
    return {
        "use_epithet": USE_EPITHET, "use_land_names": USE_LAND_NAMES,
        "epithet": epithet, "eligible": eligible,
        "fame": int(fame.get("total") or 0), "notoriety": int(fame.get("notoriety") or 0),
        "threshold": int(fame.get("threshold") or EPITHET_FAME),
        "lands": int(fame.get("lands") or 0),
        "min_lands": int(fame.get("min_lands") or EPITHET_MIN_LANDS),
        "land_threshold": int(fame.get("land_threshold") or LAND_NAME_FAME),
        "min_deeds": MIN_DEEDS, "here": here or "", "rows": rows,
    }


# --------------------------------------------------------------------------
def apply(ctx):
    store = getattr(sys, STATE_STORE_ATTR, None)
    if store is None:
        store = {
            "state": {
                "last_check": 0.0,     # 素材を照合した時刻（間引き用）
                "noted": set(),        # 1回だけ出す知らせの鍵
            },
            # 周回の鍵 -> キャッシュ（書くのはこの MOD だけ）。
            # 出し入れと錠はローダの語彙（`state.WorldStore`）。
            "worlds": WorldStore(ctx, STATE_DIRNAME,
                                 normalize=as_bucket, order=stored_bucket),
            # 編纂を回す背景スレッド。作るのは `compile_area` が出来てから。
            "worker": None,
        }
        setattr(sys, STATE_STORE_ATTR, store)
    state = store["state"]

    write = ctx.logger(LOG_BASENAME)
    owner = os.path.basename(getattr(ctx, "mod_dir", "") or "") or "317_reputation"
    worlds = store["worlds"].rebind(ctx, write)
    data_lock = worlds.lock

    def note_once(key, message):
        """同じ鍵の知らせを1回だけ残す。

        `ctx.warner` は行き先がローダの共用ログなので、
        調べ物のための記録はこちらの1本にまとめる（TECH.md §3.11.2）。
        """
        if key in state["noted"]:
            return
        state["noted"].add(key)
        write(message)

    #: 注入の結末。直前と同じ内容なら書かない（会話は1ターンに何度も回る）。
    note_inject = ctx.logger(LOG_BASENAME, dedup=True)

    # ------------------------------------------------------------ キャッシュ
    # 場所・読み・キャッシュ・書き・錠は `worlds`（`state.WorldStore`）が持つ。
    # フォルダを作るのは最初に触ったときで、`apply()` では作らない
    # （一度も評判が立っていない `state/` に空のフォルダを置かないため。TECH.md §3.11）。
    def reroll_path(key):
        # `121_` が書く「引き直しの頼み」。読む側なのでフォルダは作らない
        # （`ctx.state_path` は親を作る。TECH.md §3.11）。
        return os.path.join(ctx.state_dir, STATE_DIRNAME,
                            world_filename(key, REROLL_SUFFIX))

    def take_reroll(key):
        """引き直しの頼みがあれば消して True。消せなければ次の照合に残す。"""
        path = reroll_path(key)
        if not os.path.exists(path):
            return False
        try:
            os.remove(path)
        except OSError:
            ctx.log_exc("reputation: 引き直しの頼みを消せなかった")
            return False
        return True

    def bucket_of(key):
        """その周回のキャッシュ `{"areas": 土地別, "epithet": 世界に1つ}`。

        無ければ読み込む。錠は呼び側が持つ。
        読んだものをこの形へ均すのは `as_bucket`、書くときの形は `stored_bucket`。
        """
        return worlds.load(key)

    def flush(key, bucket):
        """控えをファイルへ。錠の中で呼ぶ。"""
        return worlds.save(key, bucket)

    def record_of(key, area_id):
        with data_lock:
            record = bucket_of(key)["areas"].get(str(area_id))
            return dict(record) if isinstance(record, dict) else None

    def epithet_record_of(key):
        """世界の二つ名の控え。まだ無ければ `None`。"""
        with data_lock:
            record = bucket_of(key)["epithet"]
            return dict(record) if isinstance(record, dict) and record else None

    def save_record(key, area_id, record):
        with data_lock:
            bucket = bucket_of(key)
            areas = dict(bucket["areas"])
            areas[str(area_id)] = ordered_record(record)
            bucket["areas"] = {aid: areas[aid]
                               for aid in sorted(areas, key=ui.id_sort_key)}
            return flush(key, bucket)

    def drop_record(key, area_id):
        """土地の控えを外す。外したら True、元から無ければ何も書かずに False。

        注入の側（`block_for`）は控えの有無しか見ないので、
        線を下回った土地の控えを残すと古い評判文が出続ける。
        残りの土地の並びは保つ（`dict` から1件抜くだけなので崩れない）。
        """
        with data_lock:
            bucket = bucket_of(key)
            if str(area_id) not in bucket["areas"]:
                return False
            areas = dict(bucket["areas"])
            del areas[str(area_id)]
            bucket["areas"] = areas
            return flush(key, bucket)

    def save_epithet(key, record):
        with data_lock:
            bucket = bucket_of(key)
            bucket["epithet"] = ordered_epithet(record)
            return flush(key, bucket)

    def fame_record_of(key):
        """世界の名声の控え。まだ無ければ `None`。"""
        with data_lock:
            record = bucket_of(key).get("fame")
            return dict(record) if isinstance(record, dict) and record else None

    def save_fame(key, fame):
        """名声の控えを書く。前と同じなら書かない（照合のたびにファイルを触らない）。"""
        with data_lock:
            bucket = bucket_of(key)
            if bucket.get("fame") == fame:
                return False
            bucket["fame"] = fame
            return flush(key, bucket)

    def notoriety_of(key):
        with data_lock:
            return dict(bucket_of(key).get("notoriety") or {})

    def refresh_fame(app, world, stats=None):
        """悪名を積み、いまの名声と悪名を数えて控える。数えた控えを返す。

        悪名は前に見た手配の重さと比べて積むので、錠の中で読み書きを1回で済ませる。
        """
        stats = material.survey(app) if stats is None else stats
        with data_lock:
            bucket = bucket_of(world)
            ledger, changed = accumulate_notoriety(bucket.get("notoriety"), stats)
            if changed:
                bucket["notoriety"] = ledger
                flush(world, bucket)
        fame = fame_summary(stats, ledger)
        before = fame_record_of(world)
        if save_fame(world, fame) and (before or {}).get("eligible") != fame["eligible"]:
            write("名声: {}（{}）".format(
                "二つ名が付く線に届いた" if fame["eligible"]
                else "二つ名が付く線に届いていない", fame_shortfall(fame)))
        return fame

    # ------------------------------------------------------------ 素材の照合
    def snapshot(app, area_id):
        """その土地の素材と、その周回の鍵。一度も訪れていない土地なら `None`。"""
        if not area_id:
            return None
        item = material.gather(app, area_id)
        if item is None:
            return None
        return {
            "kind": "area",
            "world": worlds.playthrough(app),   # 周回の鍵（世界×主人公）
            "area_id": str(area_id),
            "material": item,
            "player": material.player_name(app),
            "day": material.game_day(app),
        }

    def epithet_sources(app, mark):
        """二つ名の写しを見分ける素材。評判の立つ土地の成した事と、片付けた依頼の題名。

        二つ名は各地の評判文から編むので、写しになりうるのはその土地の素材だけ。
        編纂はワーカーのスレッドで走るので、ゲームの状態は照合の側（ここ）で読んでおく。
        """
        sources = {"achievements": [], "quests": []}
        for area_id in mark.get("qualifying") or ():
            item = material.gather(app, area_id)
            if item is None:
                continue
            sources["achievements"].extend(item.get("achievements") or [])
            sources["quests"].extend(item.get("quests") or [])
        return sources

    def job_key(job):
        return (job.get("kind", "area"), job["world"], job.get("area_id", ""))

    def note_dropped(job):
        write("編纂: 古い仕事を捨てた（{}）".format(job_key(job)))

    def enqueue(job):
        """編纂の仕事を積む。ワーカーが居なければ起こす。

        待ち行列・直列のスレッド・溢れたら古い方から捨てる・同じ鍵を二度積まない
        ・仕事が無ければ畳む、はローダの語彙（`jobs.Worker`）。
        ここに残すのは**何をログに出すか**だけ。
        """
        if not worker.enqueue(job):
            return False
        if job.get("kind") == "epithet":
            write("二つ名の編纂を予約: {}（評判の立つ土地 {}、契機 {}）".format(
                job["world"], len(job["mark"]["qualifying"]), job["why"]))
        else:
            write("編纂を予約: {} / {}（{}件の記録、印 {}、契機 {}）".format(
                job["world"], job["area_id"], deeds_count(job["material"]),
                job["fingerprint"], job["why"]))
        return True

    def check(app, why):
        """素材が変わっていれば編纂を予約する。**呼ばれても大半は何もしない。**

        照合そのものが依頼の走査を含むので、間引いてから入る。
        土地の評判 → 二つ名の順に見る。
        同じ回で両方立ったときは土地の仕事が先に並ぶので、
        直列のワーカーが評判を編んでから二つ名がそれを読める。
        """
        if app is None:
            return
        now = time.monotonic()
        if now - state["last_check"] < CHECK_INTERVAL:
            return
        state["last_check"] = now
        # 名声と悪名を先に数える（呼び名の線と二つ名の線の両方がこれを見る）。
        world = worlds.playthrough(app)
        stats = material.survey(app)
        fame = refresh_fame(app, world, stats)
        check_lands(app, why, world, stats, fame)
        check_epithet(app, why, world, stats, fame)

    def check_lands(app, why, world, stats, fame):
        """評判の立つ土地を**すべて**照合する（版6）。いま居る土地を先に積む。

        版5まではいま居る土地だけを見ていたので、評判はその土地に着くまで編まれなかった。
        読み込み・移動・日送りのたびに全部を見て、素材の変わった土地だけを積む（LLM は変わった土地の分だけ）。
        控えのある土地も見る（線を下回った土地の控えを外すため）。
        """
        here = str(material.current_area_id(app) or "")
        if LOG_MATERIAL and here:
            note_once("shape", "素材の形: " + material.describe_shape(app, here))
        with data_lock:
            known = set(bucket_of(world)["areas"])
        standing = {area_id for area_id, item in (stats or {}).items()
                    if item.get("deeds", 0) >= MIN_DEEDS or item.get("wanted")}
        for area_id in sorted(standing | known,
                              key=lambda aid: (aid != here, ui.id_sort_key(aid))):
            check_area(app, why, fame, area_id)

    def check_area(app, why, fame, area_id):
        """土地1つの評判。素材の印（fingerprint）が変わったときだけ立つ。

        呼び名の線を越えた土地で、控えにまだ呼び名の欄が無ければ、印が同じでも1回編み直す
        （版5までの控え・線を越えた直後に素材が変わらなかった回）。
        """
        shot = snapshot(app, area_id)
        if shot is None:
            return
        item = shot["material"]
        if not qualifies(item):
            note_once("thin:" + shot["area_id"],
                      "{} は素材が薄いので評判を立てない（{}件、{}）".format(
                          item.get("area_name") or shot["area_id"],
                          deeds_count(item), lawfulness_line(item)))
            # 一度立った評判も、線を下回れば外す（役場で手配を解いた・最低件数を
            # 引き上げた）。外す側は印を見ない。再び線に届けば印が無いので編み直す。
            if drop_record(shot["world"], shot["area_id"]):
                write("{} は線を下回ったので評判の控えを外した（{}）".format(
                    item.get("area_name") or shot["area_id"], shot["world"]))
            return
        mark = material.fingerprint(item)
        record = record_of(shot["world"], shot["area_id"])
        named = land_named(fame, shot["area_id"])
        if (record is not None and record.get("fingerprint") == mark
                and not (named and KEY_NICKNAME not in record)):
            return
        shot["fingerprint"] = mark
        shot["why"] = why if record is None or record.get("fingerprint") != mark else \
            "{}: 呼び名の線を越えた".format(why)
        shot["nickname"] = named
        enqueue(shot)

    def check_epithet(app, why, world, stats, fame):
        """世界の二つ名。質的状態（`epithet_mark`）が変わったときだけ立つ。

        いま居る土地が評判の立たない土地でも見る。
        依頼の完了や恩赦は、居る土地と効く土地が別でありうるため。
        名声と悪名は二つ名を切っていても数えて控える（`check` が先に数える。人物欄と評判の画面が読む）。
        """
        if not USE_EPITHET:
            return
        mark = epithet_mark(stats, notoriety_of(world))
        record = epithet_record_of(world)
        exclude = ""
        reroll = os.path.exists(reroll_path(world))
        if not fame["eligible"]:
            # 線に届く前の名は出さない。人物欄はボタンを隠すが、残った頼みは消しておく。
            if reroll and take_reroll(world):
                write("二つ名: 名声が足りないので引き直しの頼みを捨てた（{}）".format(
                    fame_shortfall(fame)))
            if state.get("rerolling"):
                state["rerolling"] = False
                view_changed()
            note_inject("二つ名: まだ付かない（{}）".format(fame_shortfall(fame)))
            return
        # 線に届く前に付いた名（版5まで）は、いままでの名として渡さない。
        fresh = bool(record) and not mark_eligible(record.get("mark"))
        if reroll:
            # 人物欄のボタンからの引き直し。質的な変化が無くても編み直す。
            # いまの名を除く指示で渡す（据え置きの指示と入れ替わる）。
            exclude = clean_epithet((record or {}).get(KEY_EPITHET, ""))
            reason = "引き直しの頼み"
        else:
            due, reason = epithet_due(record, mark)
            if not due:
                return
        queued = enqueue({"kind": "epithet", "world": world,
                          "player": material.player_name(app),
                          "day": material.game_day(app),
                          "mark": mark, "exclude": exclude, "fresh": fresh,
                          "sources": epithet_sources(app, mark),
                          "why": "{}: {}".format(why, reason)})
        if reroll and queued:
            # 積めたときだけ頼みを消す。編纂の最中・待ちの間は同じ鍵を積まないので、
            # 先に消すと頼みが黙って消えていた。残せば次の照合で積み直す
            take_reroll(world)

    # ------------------------------------------------------------------ 編纂
    def land_nickname(job, parsed, before):
        """編纂の返答から呼び名を決める。使えない名なら前の名を残す。

        写しの番人は二つ名と同じ（本人の名前・依頼の題名・成した事）に、土地の名前を足す。
        再生成で除けと言った名がそのまま返ったときも前の名のまま（もう一度押せる）。
        """
        item = job["material"]
        nickname = parsed.get(KEY_NICKNAME, "")
        area = item.get("area_name") or ""
        if nickname and (echoes_material(nickname, job["player"], item)
                         or (area and area in nickname)):
            write("呼び名: {!r} は本人の名前・土地の名前・素材の写しなので捨てた（{} / {}）".format(
                nickname, job["world"], job["area_id"]))
            nickname = ""
        exclude = clean_epithet(job.get("exclude_nickname", ""))
        if nickname and exclude and nickname == exclude:
            write("呼び名: 再生成でも同じ名 {!r} が返った（{} / {}）".format(
                nickname, job["world"], job["area_id"]))
            nickname = ""
        return nickname or before

    def compile_area(job):
        item = job["material"]
        before = record_of(job["world"], job["area_id"]) or {}
        before_nickname = clean_epithet(before.get(KEY_NICKNAME, ""))
        named = bool(job.get("nickname"))
        messages = build_messages(item, job["player"], nickname=named,
                                  current_nickname=before_nickname,
                                  exclude_nickname=job.get("exclude_nickname", ""))
        raw = llm.ask(ctx, MANAGER_COMPILE, messages, timeout=COMPILE_TIMEOUT,
                      label="reputation", write=write)
        parsed = parse_result(raw)
        if parsed is None:
            # 読めなかったときは**前の評判を残す**。
            # 空で上書きすると、素材が次に変わるまで注入が消える。
            write("編纂: 読めなかったので前の評判を残す（{} / {}）".format(
                job["world"], job["area_id"]))
            return
        record = {
            "area": job["area_id"],
            "name": item.get("area_name") or "",
            "day": job["day"],
            "updated": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "reputation": parsed[KEY_REPUTATION],
            "fingerprint": job["fingerprint"],
        }
        if job.get("keep_reputation") and clean_reputation(before.get(KEY_REPUTATION)):
            # 呼び名だけの再生成。素材は前の編纂から変わっていないので、評判の文は前のまま
            record["reputation"] = before[KEY_REPUTATION]
        if named:
            # 線を越えた土地は、名が付かなくても欄を作る（作らないと照合のたびに頼み直す）。
            record[KEY_NICKNAME] = land_nickname(job, parsed, before_nickname)
        elif KEY_NICKNAME in before:
            record[KEY_NICKNAME] = before[KEY_NICKNAME]
        if save_record(job["world"], job["area_id"], record):
            write("編纂できた: {} / {} 評判={!r}{}".format(
                job["world"], job["area_id"], record["reputation"],
                " 呼び名={!r}".format(record.get(KEY_NICKNAME, "")) if named else ""))
            view_changed()

    def epithet_entries(world, mark):
        """二つ名の編纂に載せる `(土地名, 評判文)`。**編纂済みの土地だけ**。"""
        entries = []
        for area_id in mark["qualifying"]:
            record = record_of(world, area_id)
            if record is None:
                continue
            text = clean_reputation(record.get(KEY_REPUTATION))
            if text:
                entries.append((record.get("name") or "", text))
        return entries

    def compile_epithet(job):
        """二つ名を編み直す（2段目）。入力は各地の評判文。

        直列のワーカーの中なので、同じ照合で立った土地の評判は先に編み終わっている。
        """
        world = job["world"]
        entries = epithet_entries(world, job["mark"])
        if not entries:
            # 評判文がまだ1つも無い（1段目が失敗した直後など）。
            # 印を控えないので、評判が編めた後の照合で立ち直す。
            write("二つ名: 評判文がまだ無いので見送り（{}）".format(world))
            return
        record = epithet_record_of(world) or {}
        before = clean_epithet(record.get(KEY_EPITHET, ""))
        before_desc = clean_epithet_description(record.get(KEY_DESCRIPTION, ""))
        if job.get("fresh"):
            # 線に届く前に付いた名。据え置きの候補にも、名が付かなかったときの戻り先にもしない。
            before = before_desc = ""
        exclude = clean_epithet(job.get("exclude", ""))
        length = pick_epithet_length()
        messages = build_epithet_messages(job["player"], entries,
                                          "" if exclude else before,
                                          exclude=exclude, length=length)
        if length < EPITHET_CHARS:
            write("二つ名: 狙いは{}字くらい（上限{}字）".format(
                length, EPITHET_CHARS))
        raw = llm.ask(ctx, MANAGER_EPITHET, messages, timeout=COMPILE_TIMEOUT,
                      label="reputation", write=write)
        parsed = parse_epithet(raw)
        if parsed is None:
            # 読めなかったら印も控えない（評判の編纂の失敗と同じ扱い）。
            write("二つ名: 読めなかったので前の名を残す（{}）".format(world))
            return
        epithet = parsed[KEY_EPITHET]
        description = parsed[KEY_DESCRIPTION]
        if epithet and echoes_material(epithet, job["player"], job.get("sources")):
            write("二つ名: {!r} は本人の名前か依頼の題名・成した事の写しなので捨てた（{}）"
                  .format(epithet, world))
            epithet = ""
        if epithet and exclude and epithet == exclude:
            # 除けと言った名がそのまま返った。前の名のまま（もう一度押せば再挑戦）。
            write("二つ名: 引き直しでも同じ名 {!r} が返った（{}）".format(
                epithet, world))
            epithet = ""
        if not epithet:
            # 「ふさわしい名が無い」。前の名を残しつつ、質的状態は消費と数える
            # （印を控えないと、同じ問いを照合のたびに繰り返す）。
            # 説明は名と対の内部データなので、名を残すなら説明も残す。
            epithet = before
            description = before_desc
            note = "据え置き（新しい名は無し）" if epithet else "名は付かなかった"
        else:
            if epithet == before and not description:
                description = before_desc
            note = "据え置き" if epithet == before else "改名"
        if save_epithet(world, {
                KEY_EPITHET: epithet,
                KEY_DESCRIPTION: description,
                "day": job["day"],
                "updated": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "mark": job["mark"],
        }):
            write("二つ名: {}（{}）二つ名={!r}".format(note, world, epithet))

    def compile_job(job):
        """仕事1件。土地の評判と二つ名で行き先が違うだけ。"""
        if job.get("kind") == "epithet":
            try:
                compile_epithet(job)
            finally:
                # 読めなかった・見送った回も、画面の「引き直しています」を解いて開き直す
                state["rerolling"] = False
                view_changed()
        else:
            try:
                compile_area(job)
            finally:
                if job.get("exclude_nickname") is not None:
                    state.setdefault("rerolling_lands", set()).discard(job["area_id"])
                    view_changed()

    # 編纂は背景で1件ずつ（LLM を待つのでゲームのスレッドでは回せない）。
    # 土地の仕事が先に並ぶので、直列のワーカーが評判を編んでから二つ名がそれを読める。
    # 注入し直したときは、捨てたときのログも今の世代へ付け替える。
    worker = store["worker"] = jobs.rebind(
        store["worker"]
        or jobs.Worker(ctx, compile_job, name="reputation", label="reputation",
                       key=job_key, max_pending=MAX_PENDING,
                       on_drop=note_dropped),
        ctx, compile_job, write, on_drop=note_dropped)

    # ------------------------------------------------------------------ 注入
    def block_for(app):
        """いま居る土地の注入文。無ければ空文字。**ここでは LLM を回さない。**"""
        area_id = material.current_area_id(app)
        if not area_id:
            return ""
        world = worlds.playthrough(app)
        record = record_of(world, area_id)
        if record is None:
            return ""
        epithet = nickname = ""
        if USE_EPITHET or USE_LAND_NAMES:
            # 名声の控えが無いのは版5までのファイルを読んだ直後だけ。1回数えて控える。
            fame = fame_record_of(world) or refresh_fame(app, world)
            if USE_EPITHET and fame.get("eligible"):
                epithet = (epithet_record_of(world) or {}).get(KEY_EPITHET, "")
            if land_named(fame, area_id):
                nickname = record.get(KEY_NICKNAME, "")
        return reputation_block(record, material.player_name(app),
                                material.area_name(app, area_id), epithet, nickname)

    def describe_area_args(arguments):
        """ゲームが会話に渡している `area_*` の形（1回だけ記録する）。

        「ゲームは既に `area_achievements` を渡している」を実機で確かめるための1行。
        値は長いので形だけ出す。`arguments` は modnpc の関所が名前で束ねた引数（`info["args"]`）。
        """
        parts = []
        for name in ("area_residency", "area_achievements"):
            parts.append("{}={}".format(
                name, frames.repr_value(arguments.get(name))[:80]))
        return " / ".join(parts)

    def reputation_notes(info):
        """会話5関数の相手の `profile` に足す評判。

        ローダの `modnpc` の notes から呼ばれる（複製と差し替えは関所が1回だけ行う。TECH.md §5.7）。
        素通りするときも黙らない。
        理由がログに残らないと、効いていないことに気付けない（`311_` と同じ）。
        """
        if not IN_CONVERSATION:
            return None
        label = info.get("site")
        app = info.get("app") or ui.find_app()
        if app is None:
            note_inject("{}: app が見つからない".format(label))
            return None
        if LOG_MATERIAL:
            note_once("args:{}".format(label),
                      "{} の素材引数: {}".format(label, describe_area_args(info.get("args") or {})))
        addition = block_for(app)
        if not addition:
            note_inject("{}: この土地の評判はまだ無い".format(label))
            return None
        note_inject("{}: profile に{}字を足した".format(label, len(addition)))
        return addition

    # 会話5関数へ足す文は、ローダの `modnpc` の notes に預ける（自分で包まない）。
    # 足す順は前に自分で包んでいたときの重なりと同じ（`NOTES_PRIORITY`）。
    modnpc.install(ctx, write=write)
    modnpc.register(owner, modnpc.ANY, notes=reputation_notes, priority=NOTES_PRIORITY, write=write)

    # ---------------------------------------------------------- 情景描写（任意）
    @ctx.wrap("scripts.llm.llm_manager:narrator", required=False)
    def narrator(orig, *args, **kwargs):
        """`player_profile`（第3引数）に評判を足す。

        情景描写に「行を足す」形にしないのは、`300_` の narration モードと
        同じ場所を取り合わないため（MODS.md `317_reputation`）。
        こちらが渡すのは素材で、本文をどう書くかは narrator に任せる。
        """
        if not IN_NARRATION:
            return orig(*args, **kwargs)
        try:
            app = ui.find_app()
            addition = block_for(app) if app is not None else ""
            if addition:
                if isinstance(kwargs.get("player_profile"), str):
                    merged = dict(kwargs)
                    merged["player_profile"] = (
                        kwargs["player_profile"].rstrip() + "\n\n" + addition)
                    note_inject("narrator: player_profile に{}字を足した".format(
                        len(addition)))
                    return orig(*args, **merged)
                if len(args) >= 3 and isinstance(args[2], str):
                    merged = list(args)
                    merged[2] = args[2].rstrip() + "\n\n" + addition
                    note_inject("narrator: player_profile に{}字を足した".format(
                        len(addition)))
                    return orig(*merged, **kwargs)
                note_inject("narrator: player_profile を見つけられない"
                            "（位置引数{}個）".format(len(args)))
        except Exception:
            ctx.log_exc("reputation: 情景描写への注入に失敗した")
        return orig(*args, **kwargs)

    # -------------------------------------------------- 素材を照合する場面（3つ）
    # 着いた後・日数が進んだ後はローダの窓口（`arrivals` / `durations.on_days`。TECH.md §3.3.21）
    def area_arrival(call):
        try:
            check(call.app, "到着(土地)")
        except Exception:
            ctx.log_exc("reputation: 土地への到着で失敗した")

    def facility_arrival(call):
        try:
            check(call.app, "到着(施設)")
        except Exception:
            ctx.log_exc("reputation: 施設への到着で失敗した")

    arrivals.on(owner, ctx, (arrivals.AREA,), after=area_arrival, write=write)
    arrivals.on(owner, ctx, (arrivals.FACILITY,), after=facility_arrival, write=write)

    @ctx.wrap("scripts.hud.new_hud:InstanTaleHUD.toggle_character_sheet_visibility",
              required=False, safe=True)
    def character_sheet_toggled(orig, self, *args, **kwargs):
        """人物欄の開閉。引き直しのボタンは人物欄に在るので、
        閉じた直後の照合で頼みを拾えるように間引きを1回ぶん解く。"""
        result = orig(self, *args, **kwargs)
        try:
            state["last_check"] = 0.0
            check(ui.find_app(), "人物欄")
        except Exception:
            ctx.log_exc("reputation: 人物欄の開閉で失敗した")
        return result

    def after_days(call):
        try:
            check(call.app, "日数経過")
        except Exception:
            ctx.log_exc("reputation: 日数の経過で失敗した")

    durations.on_days(owner, ctx, after=after_days, write=write)

    # ------------------------------------------------------------ 評判の画面
    # 入口は MOD 用のボタンの一覧の行だけ（TECH.md §3.3.15。画面を開くボタンを他所に置かない）。
    icon_dir = os.path.dirname(os.path.abspath(__file__))
    icon_images = tuple(os.path.join(icon_dir, name) for name in ICON_FILES)

    def close_view():
        current = store.get("view")
        store["view"] = None
        if current is not None:
            try:
                current.dismiss()
            except Exception:
                ctx.log_exc("reputation: could not close the window")

    def current_model(app):
        """いまの名声・悪名を数え直してから、画面の中身を組む（開いた瞬間の値を出す）。"""
        world = worlds.playthrough(app)
        refresh_fame(app, world)
        with data_lock:
            bucket = {key: dict(value) for key, value in bucket_of(world).items()}
        names = {str(area_id): str(getattr(area, "name", "") or area_id)
                 for area_id, area in (ui.world_areas(app) or {}).items()}
        laws = {str(key): value for key, value in
                (ui.lawfulness_by_area(getattr(app, "player", None)) or {}).items()}
        here = ui.area_id_of(ui.current_area(app))
        return view_model(bucket, names, laws, names.get(str(here), "") if here else "")

    def open_view(app, sound=True):
        model = current_model(app)
        model["rerolling"] = bool(state.get("rerolling"))
        busy = state.setdefault("rerolling_lands", set())
        for row in model["rows"]:
            row["rerolling"] = row["id"] in busy
        store["view"] = window.open_window(
            app, model, write=write, sound=sound,
            on_reroll=lambda: request_reroll(app),
            on_land_reroll=lambda area_id: request_land_reroll(app, area_id),
            on_dismiss=lambda: store.update({"view": None}))

    def toggle_view(app):
        if store.get("view") is not None:
            close_view()
            return
        if app is None:
            return
        try:
            open_view(app)
        except Exception:
            store["view"] = None
            ctx.log_exc("reputation: could not open the window")

    def refresh_view(_dt=None):
        """開いている画面を新しい中身で開き直す（表のスレッドで）。開いていなければ何もしない。"""
        state["refresh_pending"] = False
        if store.get("view") is None:
            return
        app = ui.find_app()
        if app is None:
            return
        try:
            current = store.get("view")
            store["view"] = None
            current.dismiss()
            open_view(app, sound=False)
        except Exception:
            store["view"] = None
            ctx.log_exc("reputation: could not refresh the window")

    def view_changed():
        """編纂が終わった（ワーカーのスレッドから呼ばれる）。画面が開いていれば表のスレッドで開き直す。

        読み込みの直後は土地の編纂が続けて終わるので、0.5秒まとめてから1回だけ開き直す。
        """
        if store.get("view") is None or state.get("refresh_pending"):
            return
        state["refresh_pending"] = True
        try:
            from kivy.clock import Clock
            Clock.schedule_once(refresh_view, 0.5)
        except Exception:
            state["refresh_pending"] = False

    def request_reroll(app):
        """評判の画面の「引き直す」。人物欄のさいころと同じ頼みのファイルを書き、すぐ照合する。

        頼みは `check_epithet` が拾い、いまの名を除いて編み直す（`REROLL_SUFFIX`）。
        終わったら `view_changed` が画面を開き直す。
        """
        world = worlds.playthrough(app)
        path = reroll_path(world)
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            if not ctx.write_json(path, {"reroll": True}):
                return False
        except Exception:
            ctx.log_exc("reputation: could not request a reroll")
            return False
        state["rerolling"] = True
        state["last_check"] = 0.0
        check(app, "評判の画面の引き直し")
        return True

    def request_land_reroll(app, area_id):
        """評判の画面の土地の札の再生成。その土地の呼び名を、いまの名を除いて付け直す。

        素材が前の編纂から変わっていなければ評判の文は前のまま残す（呼び名だけ）。
        変わっていれば評判の文も一緒に編み直す（古い文に新しい印を付けないため）。
        終わったら `view_changed` が画面を開き直す。
        """
        world = worlds.playthrough(app)
        record = record_of(world, area_id)
        shot = snapshot(app, area_id)
        if record is None or shot is None:
            return False
        mark = material.fingerprint(shot["material"])
        shot.update({"fingerprint": mark, "nickname": True,
                     "exclude_nickname": clean_epithet(record.get(KEY_NICKNAME, "")),
                     "keep_reputation": record.get("fingerprint") == mark,
                     "why": "評判の画面の呼び名の再生成"})
        if not enqueue(shot):
            return False
        state.setdefault("rerolling_lands", set()).add(str(area_id))
        return True

    hudicons.add(ctx, ICON_KEY, toggle_view, images=icon_images, order=ICON_ORDER,
                 label=window.TITLE, write=write)

    # ------------------------------------------------------------ 読み込みの後
    # 読み込んだ時点で評判の立つ土地をすべて照合し、変わった土地を編む（その土地に着くまで待たない）。
    def after_load(call):
        try:
            state["last_check"] = 0.0
            check(call.app, "読み込み")
        except Exception:
            ctx.log_exc("reputation: 読み込みの後の照合で失敗した")

    loads.on(owner, ctx, (loads.LOAD, loads.NEW_GAME), after=after_load, write=write)

    # 起動直後は注入のやり直し（約6秒後）と読み込みが重なり、読み込みの包みが効く前に世界が読み込まれることがある
    # （実機。起動から14秒で世界を選んだ回に読み込みの後の照合が来なかった）。
    # 注入が済んだ時点で世界が読み込まれていれば、その場で1回照合する。
    def after_inject(_dt=None):
        try:
            app = ui.find_app()
            if app is None or material.player_of(app) is None or not ui.world_areas(app):
                return
            state["last_check"] = 0.0
            check(app, "注入")
        except Exception:
            ctx.log_exc("reputation: 注入の後の照合で失敗した")

    try:
        from kivy.clock import Clock
        Clock.schedule_once(after_inject, 1.0)
    except Exception:
        pass        # ゲームの外（検査）には Kivy が無い

    ctx.log("reputation: installed (会話={} 情景描写={} 二つ名={} 最低件数={} 長さ={}"
            " 二つ名の名声={} 土地={})"
            .format(IN_CONVERSATION, IN_NARRATION, USE_EPITHET, MIN_DEEDS,
                    INJECT_DETAIL, EPITHET_FAME, EPITHET_MIN_LANDS))
