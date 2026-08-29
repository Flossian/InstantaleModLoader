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
        │                                          state/reputation/<世界名>.json
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

キャッシュも二つ名も `state/reputation/<世界名>.json` のみ（TECH.md §3.11）。
`area_history` は**読むだけ**で、`lawfulness` を書き換えない
（手配を動かすのは `309_` と `316_` の仕事）。
"""

import copy
import datetime
import os
import random
import sys
import time

from instantale_modloader import frames, jobs, llm, ui
from instantale_modloader.state import WorldStore, world_filename, world_key

from . import material

# ---- 設定（既定値は mod.json の "settings" と一致させること。
#      `tools/check_mods.py` が AST で突き合わせる）------------------------
USE_EPITHET = True        # 二つ名を編纂し、注入にも載せるか
EPITHET_CHARS = 8         # 二つ名の上限（全角）。既定8は実機の所感（MODS.md `317_reputation` の「二つ名が編み直される契機」）
EPITHET_RANDOM_LENGTH = True  # 編纂のたびに狙う長さを揺らすか（下限3〜上限）
IN_CONVERSATION = True    # 会話5関数に注入するか
IN_NARRATION = False      # 情景描写（narrator）に注入するか
MIN_DEEDS = 2             # 評判が立つ最低件数（achievements ＋ 完了した依頼）
INJECT_DETAIL = "brief"   # 注入の長さ。brief=1文 / detailed=3文まで
LOG_MATERIAL = True       # 素材の実行時の形を1回だけログに残すか

# ---- 以下は設定にしない値 -------------------------------------------------

LOG_BASENAME = "reputation.log"

#: 世界ごとのキャッシュを置くフォルダ（`state/` の下）。
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
#: 土地の数だけしか積まれないので小さくてよい。
MAX_PENDING = 4

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
RECORD_KEYS = ("area", "name", "day", "updated", "reputation", "fingerprint")

#: 二つ名の控え（世界に1つ）の鍵と並び。
#: `description` はその名が何を指すかの説明。**ゲームには出さない**内部データで、
#: 後から控えを読んだときに名の由来が分かるように編纂と一緒に書かせる。
#: `mark` は編纂したときの質的状態（`epithet_mark`）。次の契機の比較相手。
EPITHET_RECORD_KEYS = ("epithet", "description", "day", "updated", "mark")

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
#: 錠が別インスタンスになり、同じ `state/reputation/<世界>.json` を
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
    """
    if not isinstance(item, dict):
        return 0
    return len(item.get("achievements") or []) + len(item.get("quests") or [])


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
    return random.randint(EPITHET_LENGTH_FLOOR, EPITHET_CHARS)


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
        for key in (KEY_REPUTATION, KEY_EPITHET, KEY_DESCRIPTION))


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
    return {KEY_REPUTATION: reputation}


def as_bucket(data):
    """読んだものを `{"areas": 土地別, "epithet": 世界に1つ}` に均す。

    旧版のファイル（土地の控えに "epithet" が混ざっている）もこの形に収まる
    （読み側がもう見ないだけで、鍵は `ordered_record` が後ろへ回して残す）。
    形を均すだけで書き戻しはしないので、2つ目は常に False。
    """
    if not isinstance(data, dict):
        data = {}
    areas = data.get("areas")
    epithet = data.get("epithet")
    return {"areas": areas if isinstance(areas, dict) else {},
            "epithet": epithet if isinstance(epithet, dict) else {}}, False


def stored_bucket(bucket):
    """書く形。二つ名がまだ無い世界のファイルに空の欄を作らない。"""
    data = {"areas": bucket["areas"]}
    if bucket["epithet"]:
        data["epithet"] = bucket["epithet"]
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


def reputation_block(record, player, area, epithet=""):
    """注入する本文。控えが無ければ空文字（＝何も足さない）。

    二つ名は世界に1つだが、**載せるのは評判の立つ土地の中だけ**。
    評判の無い土地で二つ名だけを注入すると、
    「何も無ければ何も足さない」（MODS.md `317_reputation` の「素材とプロンプトの重なり」）が崩れる。
    """
    if not isinstance(record, dict):
        return ""
    body = clean_reputation(record.get(KEY_REPUTATION))
    if not body:
        return ""
    lines = [HEADING.format(area=area or AREA_FALLBACK, player=player), body]
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


def build_messages(item, player):
    """編纂の頼み文。**出来事を並べ直させない**（MODS.md `317_reputation` の「素材とプロンプトの重なり」）。

    ゲーム自身が `area_achievements` を会話に渡している以上、
    ここで素材を要約し直すと同じ事実が言い換えで2度載る。
    頼むのは「土地の人々が何を知っていて、どう呼んでいるか」の1点に絞る。
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
    body += [
        "",
        "返答は次の形の JSON オブジェクト1個だけとし、他には何も書かない。",
        '{"reputation": "評判の文"}',
    ]
    # 二つ名はここでは頼まない。
    # 世界に1つの2段目（`build_epithet_messages`）へ移した（MODS.md `317_reputation` の「二つ名が編み直される契機」）。
    # 写し禁止と空文字への逃げの断り（§6.3 の対策）もそちらが持つ。
    return [{"role": "user", "content": "\n".join(body)}]


# ------------------------------------------------------------ 二つ名（世界に1つ）
def epithet_mark(stats):
    """全土地の質的状態。二つ名を編み直す契機は**この印の変化だけ**（MODS.md `317_reputation` の「二つ名が編み直される契機」）。

    入れるのは3つ。
      qualifying  評判の立つ土地の一覧（増減どちらも契機。恩赦で外れる側も拾う）
      wanted      手配されている土地の一覧（0 をまたぐ反転を両方向で拾う）
      deeds       全土地の出来事の合計（前回の倍で契機。倍々なので終盤ほど動かない）

    滞在日数を入れないのは土地の印（`material.fingerprint`）と同じ理由。
    いくつから評判が立つか（`MIN_DEEDS`）はこちらの方針なので、
    `material.survey` は件数だけを返し、線はここで引く。
    """
    if not isinstance(stats, dict):
        stats = {}
    qualifying = sorted(
        (aid for aid, item in stats.items()
         if item.get("deeds", 0) >= MIN_DEEDS or item.get("wanted")),
        key=ui.id_sort_key)
    wanted = sorted((aid for aid, item in stats.items() if item.get("wanted")),
                    key=ui.id_sort_key)
    total = sum(item.get("deeds", 0) for item in stats.values())
    return {"qualifying": qualifying, "wanted": wanted, "deeds": total}


def epithet_due(record, mark):
    """二つ名を編み直すか。`(立つか, ログに書く理由)` を返す。

    ここが受け持つのは回数を絞る側（契機）。
    結果を絞る側（明確に覆らなければ同じ名を返す）は頼み文が持つ
    （`build_epithet_messages`）。二段構えの理由は MODS.md `317_reputation` の「二つ名が編み直される契機」。
    """
    if not mark["qualifying"]:
        return False, "評判の立つ土地が無い"
    old = record.get("mark") if isinstance(record, dict) else None
    if not isinstance(old, dict):
        return True, "初回"
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


# --------------------------------------------------------------------------
def apply(ctx):
    store = getattr(sys, STATE_STORE_ATTR, None)
    if store is None:
        store = {
            "state": {
                "last_inject": None,   # 同じ結末が続く間はログに書かない
                "last_check": 0.0,     # 素材を照合した時刻（間引き用）
                "noted": set(),        # 1回だけ出す知らせの鍵
            },
            # 世界名 -> キャッシュ（書くのはこの MOD だけ）。
            # 出し入れと錠はローダの語彙（`state.WorldStore`）。
            "worlds": WorldStore(ctx, STATE_DIRNAME,
                                 normalize=as_bucket, order=stored_bucket),
            # 編纂を回す背景スレッド。作るのは `compile_area` が出来てから。
            "worker": None,
        }
        setattr(sys, STATE_STORE_ATTR, store)
    state = store["state"]

    write = ctx.logger(LOG_BASENAME)
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

    def note_inject(message):
        """注入の結末。同じ結末が続く間は書かない。

        会話の LLM は1ターンに何度も回るので、毎回書くとログが会話で埋まる。
        """
        if state["last_inject"] == message:
            return
        state["last_inject"] = message
        write(message)

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
        """その世界のキャッシュ `{"areas": 土地別, "epithet": 世界に1つ}`。

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

    def save_epithet(key, record):
        with data_lock:
            bucket = bucket_of(key)
            bucket["epithet"] = ordered_epithet(record)
            return flush(key, bucket)

    # ------------------------------------------------------------ 素材の照合
    def snapshot(app):
        """いま居る土地の素材と、その世界の鍵。撮れなければ `None`。"""
        area_id = material.current_area_id(app)
        if not area_id:
            return None
        item = material.gather(app, area_id)
        if item is None:
            return None
        return {
            "kind": "area",
            "world": world_key(app),
            "area_id": area_id,
            "material": item,
            "player": material.player_name(app),
            "day": material.game_day(app),
        }

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
            return
        if job.get("kind") == "epithet":
            write("二つ名の編纂を予約: {}（評判の立つ土地 {}、契機 {}）".format(
                job["world"], len(job["mark"]["qualifying"]), job["why"]))
        else:
            write("編纂を予約: {} / {}（{}件の記録、印 {}、契機 {}）".format(
                job["world"], job["area_id"], deeds_count(job["material"]),
                job["fingerprint"], job["why"]))

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
        check_area(app, why)
        check_epithet(app, why)

    def check_area(app, why):
        """いま居る土地の評判。素材の印（fingerprint）が変わったときだけ立つ。"""
        shot = snapshot(app)
        if shot is None:
            return
        if LOG_MATERIAL:
            note_once("shape",
                      "素材の形: " + material.describe_shape(app, shot["area_id"]))
        item = shot["material"]
        if not qualifies(item):
            note_once("thin:" + shot["area_id"],
                      "{} は素材が薄いので評判を立てない（{}件、{}）".format(
                          item.get("area_name") or shot["area_id"],
                          deeds_count(item), lawfulness_line(item)))
            return
        mark = material.fingerprint(item)
        record = record_of(shot["world"], shot["area_id"])
        if record is not None and record.get("fingerprint") == mark:
            return
        shot["fingerprint"] = mark
        shot["why"] = why
        enqueue(shot)

    def check_epithet(app, why):
        """世界の二つ名。質的状態（`epithet_mark`）が変わったときだけ立つ。

        いま居る土地が評判の立たない土地でも見る。
        依頼の完了や恩赦は、居る土地と効く土地が別でありうるため。
        """
        if not USE_EPITHET:
            return
        world = world_key(app)
        mark = epithet_mark(material.survey(app))
        record = epithet_record_of(world)
        exclude = ""
        if take_reroll(world):
            # 人物欄のボタンからの引き直し。質的な変化が無くても編み直す。
            # いまの名を除く指示で渡す（据え置きの指示と入れ替わる）。
            exclude = clean_epithet((record or {}).get(KEY_EPITHET, ""))
            reason = "引き直しの頼み"
        else:
            due, reason = epithet_due(record, mark)
            if not due:
                return
        enqueue({"kind": "epithet", "world": world,
                 "player": material.player_name(app),
                 "day": material.game_day(app),
                 "mark": mark, "exclude": exclude,
                 "why": "{}: {}".format(why, reason)})

    # ------------------------------------------------------------------ 編纂
    def compile_area(job):
        item = job["material"]
        messages = build_messages(item, job["player"])
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
        if save_record(job["world"], job["area_id"], record):
            write("編纂できた: {} / {} 評判={!r}".format(
                job["world"], job["area_id"], record["reputation"]))

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
        if epithet and echoes_material(epithet, job["player"], None):
            write("二つ名: {!r} は本人の名前の写しなので捨てた（{}）".format(
                epithet, world))
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
            compile_epithet(job)
        else:
            compile_area(job)

    # 編纂は背景で1件ずつ（LLM を待つのでゲームのスレッドでは回せない）。
    # 土地の仕事が先に並ぶので、直列のワーカーが評判を編んでから二つ名がそれを読める。
    worker = store["worker"] = (
        store["worker"]
        or jobs.Worker(ctx, compile_job, name="reputation", label="reputation",
                       key=job_key, max_pending=MAX_PENDING,
                       on_drop=note_dropped)
    ).rebind(ctx, compile_job, write)

    # ------------------------------------------------------------------ 注入
    def block_for(app):
        """いま居る土地の注入文。無ければ空文字。**ここでは LLM を回さない。**"""
        area_id = material.current_area_id(app)
        if not area_id:
            return ""
        world = world_key(app)
        record = record_of(world, area_id)
        if record is None:
            return ""
        epithet = (epithet_record_of(world) or {}).get(KEY_EPITHET, "")
        return reputation_block(record, material.player_name(app),
                                material.area_name(app, area_id), epithet)

    def describe_area_args(args, kwargs):
        """ゲームが会話に渡している `area_*` の形（1回だけ記録する）。

        「ゲームは既に `area_achievements` を渡している」を実機で確かめるための1行。
        値は長いので形だけ出す。
        """
        parts = []
        for name in ("area_residency", "area_achievements"):
            parts.append("{}={}".format(
                name, frames.repr_value(kwargs.get(name))[:80]))
        parts.append("位置引数{}個".format(len(args)))
        return " / ".join(parts)

    def with_reputation(label, args, kwargs):
        """会話5関数の第4引数（NPC）を浅く複製し、`profile` に評判を足す。

        素通りするときも黙らない。
        理由がログに残らないと、効いていないことに気付けない（`311_` と同じ）。
        """
        npc = kwargs.get("character_instance")
        if npc is None and len(args) >= 4:
            npc = args[3]
        if npc is None:
            note_inject("{}: character_instance が無い（args={}, kwargs={}）".format(
                label, len(args), sorted(kwargs)))
            return args, kwargs
        app = ui.find_app()
        if app is None:
            note_inject("{}: app が見つからない".format(label))
            return args, kwargs
        if LOG_MATERIAL:
            note_once("args:" + label,
                      "{} の素材引数: {}".format(label,
                                                describe_area_args(args, kwargs)))
        addition = block_for(app)
        if not addition:
            note_inject("{}: この土地の評判はまだ無い".format(label))
            return args, kwargs
        base = getattr(npc, "profile", "")
        if base is None:
            base = ""
        if not isinstance(base, str):
            note_inject("{}: profile が {} なので足せない".format(
                label, type(base).__name__))
            return args, kwargs
        try:
            clone = copy.copy(npc)
        except Exception as exc:
            note_inject("{}: {} を複製できない（{}）".format(
                label, type(npc).__name__, type(exc).__name__))
            return args, kwargs
        clone.profile = (base.rstrip() + "\n\n" + addition
                         if base.strip() else addition)
        note_inject("{}: profile に{}字を足した".format(label, len(addition)))
        if "character_instance" in kwargs:
            merged = dict(kwargs)
            merged["character_instance"] = clone
            return args, merged
        merged = list(args)
        merged[3] = clone
        return tuple(merged), kwargs

    def inject(orig, label, args, kwargs):
        """引数を組み直してから元の関数へ。元の関数は必ず1回だけ呼ぶ。"""
        if IN_CONVERSATION:
            try:
                args, kwargs = with_reputation(label, args, kwargs)
            except Exception:
                ctx.log_exc("reputation: 会話への注入に失敗した")
        return orig(*args, **kwargs)

    @ctx.wrap("scripts.llm.llm_manager:conversation_starter", required=False)
    def conversation_starter(orig, *args, **kwargs):
        return inject(orig, "starter", args, kwargs)

    @ctx.wrap("scripts.llm.llm_manager:conversation_starter_in_quest",
              required=False)
    def conversation_starter_in_quest(orig, *args, **kwargs):
        return inject(orig, "starter[quest]", args, kwargs)

    @ctx.wrap("scripts.llm.llm_manager:conversation_facilitator", required=False)
    def conversation_facilitator(orig, *args, **kwargs):
        return inject(orig, "facilitator", args, kwargs)

    @ctx.wrap("scripts.llm.llm_manager:conversation_facilitator_after_retrieval",
              required=False)
    def conversation_facilitator_after_retrieval(orig, *args, **kwargs):
        return inject(orig, "facilitator[retrieval]", args, kwargs)

    @ctx.wrap("scripts.llm.llm_manager:conversation_facilitator_in_quest",
              required=False)
    def conversation_facilitator_in_quest(orig, *args, **kwargs):
        return inject(orig, "facilitator[quest]", args, kwargs)

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
    @ctx.wrap("__main__:AreaMoveManager.execute", required=False, safe=True)
    def area_arrival(orig, self, choice_text=None, *args, **kwargs):
        result = orig(self, choice_text, *args, **kwargs)
        try:
            check(getattr(self, "app", None) or ui.find_app(), "到着(土地)")
        except Exception:
            ctx.log_exc("reputation: 土地への到着で失敗した")
        return result

    @ctx.wrap("__main__:MovePhaseManager.move_phase", required=False, safe=True)
    def facility_arrival(orig, self, *args, **kwargs):
        result = orig(self, *args, **kwargs)
        try:
            check(getattr(self, "app", None) or ui.find_app(), "到着(施設)")
        except Exception:
            ctx.log_exc("reputation: 施設への到着で失敗した")
        return result

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

    @ctx.wrap("__main__:InstantaleApp.elapse_days", required=False, safe=True)
    def elapse_days(orig, self, days=None, *args, **kwargs):
        result = orig(self, days, *args, **kwargs)
        try:
            check(self, "日数経過")
        except Exception:
            ctx.log_exc("reputation: 日数の経過で失敗した")
        return result

    ctx.log("reputation: installed (会話={} 情景描写={} 二つ名={} 最低件数={} 長さ={})"
            .format(IN_CONVERSATION, IN_NARRATION, USE_EPITHET, MIN_DEEDS,
                    INJECT_DETAIL))
