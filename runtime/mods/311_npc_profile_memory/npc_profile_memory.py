# -*- coding: utf-8 -*-
"""会話から NPC の素性を書き足していく。**ゲームの素のプロフィールは消さない。**

ゲームが NPC に持たせている `profile` / `personality` は世界生成の時に決まり、
その後の会話でどれだけ人となりが見えても増えない。人生ログ（`life_log`）は
出来事の記録で、「何が好きか」「誰と繋がっているか」を引くようには出来ていない。

一方、**会話の出来事そのものはゲーム自身が覚えている**。会話を正規の経路で
閉じると `conversation_resolver` が要約を `current_log` へ追記し、それは同じ
相手との次の会話のプロンプトに毎回全文載る（GAME.md §2.25・`213_` の実測。
ただし正規以外の抜け方で要約が走るかは未実測 ― 同節の注意を参照）。だから
この mod まで出来事のあらすじを覚えると、同じ事実が `current_log`・注入
プロフィール・`about_player` の3箇所で同一プロンプトに並ぶ。3者は言い換えの
関係なので `102_` でも畳めない。そこで抽出には**出来事の経過を書かない**と
指示している（`build_messages`）― この mod が足す価値は「何があったか」
ではなく「どういう人物か」で、出来事はゲームの記録に任せる。

```
    プレイヤーが1行入力
        │
        ├─ conversation_facilitator ── 注入 ── NPC の profile に控えを足す
        │        │                              （人物像 ＋ プレイヤーへの認識）
        │        └─ NPC の返答
        │
        └─ 返答が描画された次のフレーム
                 └─ 自前 LLM ── JSON で受け取る ── state/npc_profiles/<世界名>.json
```

控えは1人につき2欄ある:

    profile        その人物自身の像（性格・嗜好・経歴・目標・秘密）
    about_player   **その人物から見たプレイヤー**（呼び方・感情・約束・貸し借り）

分けているのは、片方に混ぜると要約のたびに弱い方が食われるため。「前に頼んだ
件を覚えている」という手応えは `about_player` が担っていて、これが人物像の
文章に溶けると真っ先に消える。

3つめは**いつの話か**。宿泊は数ヶ月飛ぶことがあるのに、ゲームが会話に渡すのは
人生ログの日付だけで、「前にこの人と話したのがいつか」はどこにも無い。だから
日が飛んだ次の会話が数秒前の続きのように始まる。最後に話したゲーム内の日
（`world.days_elapsed`）を控えて経過を添えるが、**日数だけを渡して解釈は
指示で縛る** ― そのまま見せると毎回「久しぶり」になるので、「同じ土地に
留まっている間は日々見かけているはず」を併せて言う。実際に離れていたかは
ゲーム自身が渡している滞在歴が語れるので、こちらは判定しない。

この2欄はどちらも**毎回まるごと書き直される**ので、統合のたびに細部が落ちる。
落ちた分の拠り所が3つめの欄 `facts`（追記専用）で、次の抽出に差し戻して
書き戻させる（`recent_facts`）。ゲームの記憶もこの mod の2欄も書き換えで
更新される以上、**追記でしか伸びない記録はここだけ**になる。

## この mod が守っている決め事

**ゲームのセーブ構造に独自キーを足さない**（TECH.md §6）。控えは
`state/npc_profiles/<世界名>.json` に置く ― `out/` は世界を跨いで残るので、
世界ごとにファイルを分けないと別の世界の人物像が湧く（`306_` と同じ）。

**注入は NPC の複製の `profile` にだけ足す。** `conversation_starter` /
`conversation_facilitator` / `..._after_retrieval` / `..._in_quest` /
`conversation_starter_in_quest` は**先頭4引数が同じ並び**
（`messages, character_life_log, player, character_instance`）なので、第4引数の
NPC を浅く複製し、複製の `profile` を拡張すれば全経路を賄える。ゲーム世界に
居る NPC 本体も `messages` も触らない ― 本体を書き換えるとセーブや別スレッドへ
漏れ、履歴を書き換えると要約・関係性の更新に波及する。

**抽出は専用ワーカーで順番に回す。** 返答後の次フレームでは、会話と NPC の
文字列をコピーしてキューへ渡すだけ。LLM 待ちの間も Kivy のメインスレッドを
止めない。続けて来たターンは直列に処理し、後の更新は前の控えを読む。

**安全側の倒し方は「変更しない」。** 捏造された素性が永続化されるのが最悪の
壊れ方なので、空応答・LLM 不在・例外・読めない返却は既存の控えを維持する。

## 受け取りは JSON。**ゲームの構造化出力の経路は使わない**

抽出の結果は「更新後の全文」だけでなく「変わったのか」「何が新しく分かったのか」
も要る。以前は日本語の言い回し（「変更なし」「新しく分かった事は無い」）を
照合していたが、これは小さいモデルほど揺れる。そこで **JSON オブジェクト1個**
を出させて読む（`parse_result`）。

`send_request(manager_name, message, structure, ...)` に pydantic モデルを渡す
経路はゲーム自身が持っているが、こちらからは使わない:

- 引数の形を間違えると例外がゲームの内部スレッドで上がり、**呼び出しが
  永久に返らない**（GAME.md §2.12）。この mod の抽出は1本のワーカーで
  直列に回しているので、1回返らないと以後の抽出が全部止まる
- 構造化経路は空 `Literal[]` と `$defs` の肥大という既知の壊れ方を抱えている
  （`105_` / `203_`）。他人のスキーマの都合をこの mod が背負う理由が無い

`send_request_with_no_structure` は `str` を返すだけで、返らない経路も
スキーマも増えない。読めない返却は「変更しない」に倒せば済む。

## 覚えたことは他の mod も読む。**渡すのはファイルで、関数ではない**

依頼の文面を作る `301_` は、依頼主の人物像をこの mod の控えから読む。ただし
**mod が mod を import することはしない**（TECH.md §3.2.3 / §3.7 の作法）。
ローダは mod を `instantale_mod_<フォルダ名>` で登録するので、番号を振り直した
瞬間に名前で掴む側が壊れる。共有してよいのはローダの `instantale_modloader.*`
だけで、mod どうしは**同じ場所を読む**ことで繋がる。

そのため `state/npc_profiles/<世界名>.json` の形は、この mod の内部表現ではなく
**mod をまたいだ取り決め**として扱う（MODS.md の `311_` の項に書いてある）。
鍵の並びを `RECORD_KEYS` で固定しているのはこのため。読む側はファイルが無ければ
何も足さない ― この mod を切っていても、まだ一度も会話していなくても成立する。
"""

import copy
import datetime
import json
import os
import queue
import sys
import threading
import time

from instantale_modloader import llm, ui
from instantale_modloader.state import world_filename, world_key

# ---- 設定（既定値は mod.json の "settings" と一致させること。
#      `tools/check_mods.py` が AST で突き合わせる）------------------------
CONVERSATION_TURNS = 8     # 抽出に載せる直近のやり取りの数
INJECT_CHARS = 1200        # 抽出LLMへの目標長（要約で収める。保存・注入では切らない）
RECORD_PLAYER_MEMORY = True   # 「その人物から見たプレイヤー」を別欄で覚えるか
FACT_LOG_LIMIT = 200       # 判明した事実の追記専用ログの上限件数（0 で記録しない）
TELL_ELAPSED_DAYS = True   # 前に話してからのゲーム内日数を会話に伝えるか

LOG_BASENAME = "npc_profile.log"

# 世界ごとの控えを置くフォルダ。`state/` の下（`ctx.state_path`）。ログと同じ
# `out/` に置いていたが、あちらは消してよい場所なので、掃除のつもりで消した人の
# 「覚えていたはずの人物像」まで飛んでいた。`301_` も同じ名前でここを読む。
STATE_DIRNAME = "npc_profiles"

# 世界ごとのファイル名は `instantale_modloader.state.world_filename` が作る。
# ここに同じ規則を写さないこと（`301_` が同じファイルを読むので、1文字でも
# ずれると引けなくなる。TECH.md §3.2.3）。

# 自前の `manager_name`。これを付けると自分のプロンプトも
# `output_data/<世界>/<PC>/<manager_name>/N.json` に残り、抽出の検証が
# オフラインでできる（GAME.md §2.12）。
MANAGER_EXTRACT = "mod_npc_profile_extract"

# 旧版 `slots` の読み取り順。移行にだけ使い、新しいプロフィールは分類しない。
LEGACY_SLOTS = ("好み", "嫌悪", "経歴", "人間関係", "目標", "秘密", "約束")

# 新情報が無いときの応答。**JSON が読めなかったときの受け皿**でだけ使う。
# 普段は `{"changed": false}` で判断する（`parse_result`）。
NO_CHANGE = "変更なし"
EMPTY_WORDS = ("なし", "特になし", "不明", "無し", "該当なし", "none", NO_CHANGE)

PROFILE_HEADING = "【会話から形成された追加プロフィール】"
ABOUT_PLAYER_HEADING = "【この人物から見た{player}】"

# 控えの鍵と、ファイルに書くときの並び。**順番を保つ**（増えた鍵を後ろに足すだけ
# にすると、後から見比べたときに人物ごとの形が揃う）。
RECORD_KEYS = ("name", "updated", "profile", "about_player", "facts", "day")

# 最後に話したゲーム内日（`world.days_elapsed`。GAME.md §2.16）。控えの鍵は
# **末尾に足す**（上の註のとおり、既にある鍵の位置を動かさない）。
DAY_KEY = "day"

# 経過日数の但し書き。**日数だけを渡して、解釈の仕方は指示で与える。**
# 宿泊は数ヶ月飛ぶことがあるので、日数をそのまま見せると必ず「久しぶり」に
# なる。一方で同じ街に居る限り日々顔は合わせているはずで、そこはゲーム自身が
# 渡している滞在歴（`area_residency`）が語れる。だからこちらは
# 「地続きに再開しない」「不在明けの再会にもしない」の2つだけを言う。
ELAPSED_HEADING = "【前に{player}と話してからの日数】"
ELAPSED_BODY = (
    "{days}日。前回の会話はこの日数ぶん前の出来事なので、続きをこの場で"
    "そのまま再開しているかのようには話さない。ただし同じ土地に留まっている"
    "間は日々見かけているはずなので、長く離れていた相手との再会のようにも"
    "扱わない。")

# 抽出LLMに書かせる JSON の鍵。
KEY_CHANGED = "changed"
KEY_PROFILE = "profile"
KEY_ABOUT_PLAYER = "about_player"
KEY_NEW_FACTS = "new_facts"

# `about_player` の目標長。`INJECT_CHARS` から導く（設定を増やさない）。
ABOUT_PLAYER_RATIO = 2

# 抽出のときに差し戻す「判明済みの事実」の上限（件数と文字数）。設定にはしない
# ― 増やすほど落ちた事実が戻りやすいが、抽出の頼み文が伸びる。1人あたりの
# `facts` は数件〜20件程度なので、40件あればほぼ全部が載る。
FACT_RECALL = 40
FACT_RECALL_CHARS = 1500
FACTS_HEADING = "【既に記録した事実】"

# 構造化出力に渡す制限時間（秒）。**必ず渡す。** 抽出は1本のワーカーで直列に
# 回しているので、1回返らないと以後の抽出が全部止まる（GAME.md §2.12）。
EXTRACT_TIMEOUT = 120

# 待ち行列に積んだままにする仕事の上限。ワーカーが（返らない推論などで）
# 止まったときに、会話を続けるだけで際限なく溜まるのを防ぐ。溢れたら**古い方**
# を捨てる ― 新しい会話ほど人物像に効くため。
MAX_PENDING = 8

# 抽出に載せる書き起こしの長さ（`306_` と同じ考え。長すぎると要点が薄まる）。
CONVERSATION_CHARS = 2000

# ワーカーと控えの置き場所。**`apply()` の中で作ってはいけない**（TECH.md §3.4）。
# `apply()` は再注入と遅延当て直しで最大8回走り、そのたびに `state["worker"]` が
# `None` に戻る。前の世代のワーカーがまだ生きている間に2本目が起動し、しかも
# `data_lock` が別インスタンスになるので、2本が同じ
# `state/npc_profiles/<世界>.json` を排他なしで read-modify-write できてしまう。
# 注入し直すとこのモジュール自体が読み込み直されるため、モジュール変数では
# 足りない。`sys` に置けば世代をまたいで同じ1組を共有できる（`118_` と同じ手）。
STATE_STORE_ATTR = "__instantale_npc_profile_memory_store__"


def _text(value, limit=200):
    if value is None:
        return ""
    if not isinstance(value, str):
        value = str(value)
    value = value.strip()
    return value if len(value) <= limit else value[:limit] + "…"


def ordered_record(record):
    """控え1件を `RECORD_KEYS` の並びに直す。**知らない鍵は落とさず後ろへ回す。**

    並びを固定するのは、この JSON が mod をまたいだ取り決めだから（`301_` が
    読む）。人物ごとに鍵の順が違うと、差分を見たときに何が増えたのか分からない。
    旧版の `slots` のような知らない鍵を捨てないのは、移行を1度きりにせず
    何度でもやり直せるようにするため。
    """
    if not isinstance(record, dict):
        return {}
    out = {key: record[key] for key in RECORD_KEYS if key in record}
    for key, value in record.items():
        if key not in out:
            out[key] = value
    return out


def elapsed_days(record, today):
    """前に話してからのゲーム内日数。分からない・同じ日・巻き戻りなら `None`。

    巻き戻り（古いセーブを読み込んだ）で負になった場合も `None` に倒す。
    「-30日ぶりだ」と言わせるより、何も言わせない方が害が小さい。
    """
    if not isinstance(record, dict) or today is None:
        return None
    before = record.get(DAY_KEY)
    if isinstance(before, bool) or not isinstance(before, (int, float)):
        return None
    days = int(today) - int(before)
    return days if days > 0 else None


def elapsed_note(days, player_name):
    """経過日数の但し書き。日数が無ければ空文字。"""
    if not days:
        return ""
    return "{}\n{}".format(ELAPSED_HEADING.format(player=player_name),
                           ELAPSED_BODY.format(days=days))


def recent_facts(record):
    """控えの `facts` から、抽出に差し戻すぶんを古い順で返す。

    **人物像は毎回まるごと書き直される。** 統合のたびに細部が落ちるので、
    一度確定した事実でも数ターン後には本文から消えている。落ちた事実は
    `facts` に残っているのに、**抽出LLMはそれを見ていなかった**。だから
    書き戻せず、しかも既に記録した事実かどうかも判らないので同じ事実を
    毎ターン報告し直していた（同じ内容の言い換えが4回並ぶ）。ここで
    差し戻すと、その両方が同時に閉じる。

    件数と文字数の両方で頭打ちにし、溢れたら**古い方**から落とす。
    """
    log = record.get("facts") if isinstance(record, dict) else None
    if not isinstance(log, list):
        return []
    texts = []
    for item in reversed(log):
        # 追記の形は `{"at":…, "text":…}`。素の文字列で書かれていても拾う
        # （このファイルは mod をまたいだ取り決めなので、形を決めつけない）。
        text = item.get("text") if isinstance(item, dict) else item
        if isinstance(text, str) and text.strip():
            texts.append(text.strip())
        if len(texts) >= FACT_RECALL:
            break
    kept, total = [], 0
    for text in texts:          # 新しい方から詰める
        total += len(text) + 2
        if total > FACT_RECALL_CHARS:
            break
        kept.append(text)
    kept.reverse()              # 書き出すのは古い順
    return kept


def _strip_code_fence(body):
    """```json … ``` で包まれていたら中身だけにする。"""
    if not body.startswith("```"):
        return body
    body = body[3:]
    end = body.rfind("```")
    if end >= 0:
        body = body[:end]
    body = body.strip()
    for tag in ("json\n", "JSON\n", "text\n", "markdown\n"):
        if body.startswith(tag):
            return body[len(tag):].strip()
    return body


def _looks_like_json_attempt(body):
    """JSON を書こうとして失敗した返却か。

    そうであれば**受け皿へ落とさない**。丸ごと人物像として保存すると、
    壊れた JSON がそのままプロフィールになって以後の会話に注入される。
    """
    return "{" in body and ('"' + KEY_PROFILE + '"' in body
                            or "'" + KEY_PROFILE + "'" in body
                            or '"' + KEY_CHANGED + '"' in body)


def parse_result(result):
    """抽出LLMの返却を控えの更新内容に直す。

    戻り値は次のいずれか:

        None                    読めなかった。**何も変更しない**
        {}                      変更なし（`changed` が false）
        {"profile": str, "about_player": str, "new_facts": [str, ...]}

    JSON を1個だけ書かせているが、モデルは前置きを足すことも囲みを付けることも
    ある。前後の地の文は落とし、最初の `{` から最後の `}` までを読む。
    """
    if not isinstance(result, str):
        return None
    body = _strip_code_fence(result.strip())
    if not body:
        return None

    start, end = body.find("{"), body.rfind("}")
    data = None
    if 0 <= start < end:
        try:
            data = json.loads(body[start:end + 1])
        except Exception:
            data = None
    if not isinstance(data, dict):
        # JSON のつもりで書かれた壊れた返却は捨てる。素の文章なら受け皿へ。
        return None if _looks_like_json_attempt(body) else _parse_plain(body)
    return normalize_update(data)


def normalize_update(data):
    """辞書1つを控えの更新内容に直す。**構造化出力と JSON の共通の出口。**

    どちらの経路で来たかによって受け入れる形が変わらないようにする。
    戻り値は `parse_result` と同じ（`{}` は変更なし）。
    """
    if not isinstance(data, dict):
        return None
    if not _truthy(data.get(KEY_CHANGED, True)):
        return {}
    update = {}
    for key in (KEY_PROFILE, KEY_ABOUT_PLAYER):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            update[key] = value.strip()
    facts = data.get(KEY_NEW_FACTS)
    if isinstance(facts, list):
        update[KEY_NEW_FACTS] = [fact.strip() for fact in facts
                                 if isinstance(fact, str) and fact.strip()]
    # 何を書くのか1つも決まっていないなら、変更なしと同じに倒す。
    if not update.get(KEY_PROFILE) and not update.get(KEY_ABOUT_PLAYER):
        return {}
    return update


def _truthy(value):
    """`changed` の値。文字列で `"false"` と書いてくるモデルにも耐える。"""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() not in ("false", "no", "0", "", "なし", NO_CHANGE)
    return bool(value)


def _parse_plain(body):
    """JSON を書かなかったモデルのための受け皿。本文を丸ごと人物像として扱う。

    JSON を出させる前の版と同じ判定。**新しい判断はここに足さない** ―
    ここが太ると「どちらの経路で決まったのか」が読めなくなる。
    """
    plain = body.strip(" 　。()（）「」[]【】").lower()
    if plain in EMPTY_WORDS:
        return {}
    if (("新たに判明" in body or "新しく分かった" in body)
            and any(word in body for word in ("無い", "ない", "ありません",
                                              "存在しません", "見当たりません"))):
        return {}
    return {KEY_PROFILE: body}


def apply(ctx):
    # 世界ごとの控えの置き場。**ここで1回だけ引く**のが要点で、`ctx.state_path()`
    # は `out/` に同じ名前が在れば移してくるので、フォルダごと引き取れる
    # （1ファイルずつ移すと、まだ触っていない世界の控えが `out/` に残り、
    # `known_world_files()` の一覧が実際より少なく見える）。
    state_dir = ctx.state_path(STATE_DIRNAME)
    # 世代をまたいで1組だけ持つ（STATE_STORE_ATTR の説明を参照）。
    store = getattr(sys, STATE_STORE_ATTR, None)
    if not isinstance(store, dict):
        store = {
            "state": {
                "warned_world": False,  # 世界名で控えが引けなかったことを1度だけ残す
                "last_inject": None,    # 直前に書いた注入の結末（同じ理由を繰り返さない）
                "last_extract_skip": None,  # 抽出を始められない理由の連続重複を抑える
                "worker": None,         # 抽出専用ワーカー（仕事が無ければ終了する）
            },
            "cache": {"buckets": {}},   # 世界名 -> 控え（書くのはこの mod だけ）
            "jobs": queue.Queue(),
            "data_lock": threading.RLock(),
            "worker_lock": threading.Lock(),
            "log_lock": threading.Lock(),
        }
        setattr(sys, STATE_STORE_ATTR, store)
    state = store["state"]
    cache = store["cache"]
    jobs = store["jobs"]
    data_lock = store["data_lock"]
    worker_lock = store["worker_lock"]
    log_lock = store["log_lock"]

    write = ctx.logger(LOG_BASENAME)

    # ボタンは出さないので `mark` は要らない。`schedule` と例外の握りだけ借りる。
    screen = ui.Screen(ctx, write, tag="npc profile")

    # ------------------------------------------------------------ 世界と人物

    def character_of(app, npc_id):
        characters = getattr(getattr(app, "world", None), "characters", None)
        if isinstance(characters, dict) and npc_id:
            return characters.get(str(npc_id))
        return None

    def name_of(app, npc_id):
        return _text(getattr(character_of(app, npc_id), "name", ""), 40) or "その相手"

    def npc_id_of(app, character_instance):
        """相手の id。**`world.characters` の鍵から引く。`.id` には頼らない。**

        `world.characters` は id で引ける辞書になっている（`306_` の
        `character_of` がこの形で名前を引けている）。一方
        `character_instance.id` が在るかは確かめられていない ― 無ければ
        `getattr` は黙って None を返し、**注入が理由も残さず素通りする**。
        確かな方を先に試し、属性は保険に下げる。
        """
        characters = getattr(getattr(app, "world", None), "characters", None)
        if isinstance(characters, dict):
            for key, value in characters.items():
                if value is character_instance:
                    return str(key)
        value = getattr(character_instance, "id", None)
        return str(value) if value is not None else ""

    def note_inject(message):
        """注入の結末を残す。**同じ結末が続く間は書かない。**

        会話の LLM は1ターンに何度も回るので、毎回書くとログが会話で埋まる
        （`306_` の `last_skip` と同じ手）。
        """
        if state["last_inject"] == message:
            return
        state["last_inject"] = message
        write(message)

    def note_extract_skip(message):
        """抽出前の早期終了を1回だけ残す。正常にキューへ積めば次回も記録する。"""
        if state["last_extract_skip"] == message:
            return
        state["last_extract_skip"] = message
        write("extract skipped: " + message)

    # ------------------------------------------------------------ 控えの読み書き
    def state_path_for(key):
        # フォルダを作るのはここ（`ctx.state_path` が親を作る）。apply() では
        # 作らない ― 一度も会話していない人の `state/` に空のフォルダを置かない。
        return ctx.state_path(STATE_DIRNAME, world_filename(key))

    def known_world_files():
        """診断用。ディレクトリにある世界ファイル名（拡張子なし）の一覧。"""
        try:
            names = sorted(
                name[:-5] for name in os.listdir(state_dir)
                if name.endswith(".json") and os.path.isfile(
                    os.path.join(state_dir, name)))
        except Exception:
            return []
        return names

    def load_bucket(key):
        """1世界分の控え `{npc_id: レコード}`。**一度読んだら覚えておく。**

        注入のフックは LLM を呼ぶたびに走るので、そのたびに JSON を読み直すと
        会話1ターンで何度もディスクを叩くことになる。書くのはこの mod だけ
        なので、書いた内容をそのまま控えれば足りる（`306_` と同じ）。
        """
        with data_lock:
            bucket = cache["buckets"].get(key)
            if bucket is not None:
                return bucket
            path = state_path_for(key)
            # 「無い（初回）」と「在るのに読めない（ロック・破損）」を同じ {} に
            # 倒さない。後者を黙って倒すと、次の save_bucket が空に近い正本を
            # **無傷で**作る ― 記録だけは必ず残す（ctx.read_json）。
            data = ctx.read_json(path, {})
            bucket = data if isinstance(data, dict) else {}
            cache["buckets"][key] = bucket
            return bucket

    def save_bucket(key, bucket):
        """1世界分を書き出す。**書けたか書けなかったかの2つしか起こさない。**

        書き方の理屈（隣に書いてから差し替える／落ちても壊れない）は
        `ctx.write_json()` に寄せてある。ここが素朴な `open(path, "w")` だと、
        `json.dump` の途中で落ちた瞬間にその世界の控えが壊れ、`load_bucket` は
        壊れたものを黙って `{}` に倒すので、次の更新で1人ぶんだけが書かれて
        **他の人物の記憶が消えたことにも気付けない**。
        """
        with data_lock:
            cache["buckets"][key] = bucket
            path = state_path_for(key)
            ordered = {npc_id: ordered_record(record)
                       for npc_id, record in bucket.items()}
            return ctx.write_json(path, ordered)

    def bucket_of(app):
        """この世界の控え `{npc_id: レコード}`。

        世界の見分けは名前で行っている（`306_` と同じ制約）。**名前は外部の
        データ改変ツールで書き換えられる**ので、書き換えられた世界では控えが
        行方不明になる。名前に代わる安定した id が見つかっていないので名前の
        ままにしてあるが、そうなったことが分かるように1度だけ残す。
        """
        key = world_key(app)
        bucket = load_bucket(key)
        if not bucket and not state["warned_world"]:
            others = known_world_files()
            if others:
                state["warned_world"] = True
                write("no profiles for world {!r}; directory has {}".format(
                    key, others))
        return bucket

    def flatten_slots(slots):
        """旧版の分類別控えを、情報を落とさず1本のプロフィールへ移す。"""
        if not isinstance(slots, dict):
            return ""
        lines = []
        for label in LEGACY_SLOTS:
            facts = slots.get(label)
            if not isinstance(facts, list):
                continue
            values = [fact.strip() for fact in facts
                      if isinstance(fact, str) and fact.strip()]
            if values:
                lines.append("{}: {}".format(label, "／".join(values)))
        return "\n".join(lines)

    def record_for(key, npc_id, npc_name):
        """世界名と人物 id だけで引く。ワーカーからゲームオブジェクトを触らない。

        旧版の分類別 `slots` しか無い控えは、ここを最初に通ったときに
        `profile` へ移す。**移した後も `slots` は消さない**（この mod を
        古い版に戻しても遊べるように）。
        """
        with data_lock:
            bucket = load_bucket(key)
            record = bucket.get(str(npc_id))
            if not isinstance(record, dict):
                return {}
            profile = record.get("profile")
            if isinstance(profile, str) and profile.strip():
                return record
            profile = flatten_slots(record.get("slots"))
            if not profile:
                return record
            record["profile"] = profile
            if save_bucket(key, bucket):
                write("migrated: {!r} ({}) slots -> profile ({} chars)".format(
                    npc_name, npc_id, len(profile)))
            return record

    def profile_for(key, npc_id, npc_name):
        """人物像の本文だけ。"""
        return _field(record_for(key, npc_id, npc_name), "profile")

    def _field(record, name):
        value = record.get(name) if isinstance(record, dict) else None
        return value.strip() if isinstance(value, str) and value.strip() else ""

    def record_of(app, npc_id):
        """この世界のこの人物の控え。旧 `slots` は初回に自動移行する。"""
        key = world_key(app)
        if not load_bucket(key):
            bucket_of(app)  # 世界名不一致の診断だけ残す
            return {}
        return record_for(key, npc_id, name_of(app, npc_id))

    def append_facts(record, facts):
        """判明した事実を追記専用で残す。**要約統合で消えた分の控え。**

        `profile` は毎回まるごと書き直されるので、統合の過程で落ちた事実は
        どこにも残らない。捏造を疑ったときに「いつの会話で出たのか」を辿れる
        ようにしておく。上限を超えたら古い方から落とす（増え続けさせない）。

        **この控えは次の抽出に差し戻す**（`recent_facts`）。この mod と
        ゲームを通じて、書き換えではなく追記で伸びる記録はここだけなので、
        落ちた事実を書き戻せる拠り所もここしかない。
        """
        if FACT_LOG_LIMIT <= 0 or not facts:
            return
        log = record.get("facts")
        if not isinstance(log, list):
            log = []
        stamp = datetime.datetime.now().isoformat(timespec="seconds")
        known = {item.get("text") for item in log if isinstance(item, dict)}
        for fact in facts:
            if fact in known:
                continue
            known.add(fact)
            log.append({"at": stamp, "text": fact})
        record["facts"] = log[-FACT_LOG_LIMIT:]

    def update_record(key, npc_id, npc_name, update, day=None):
        """抽出の結果を控えへ書く。**空・変更なしなら既存を維持する。**

        日も一緒に書く ― 控えを起こすのはここだけなので、`stamp_day` が
        作れない初回の日はここで入る。
        """
        if not update:
            return
        profile = update.get(KEY_PROFILE, "")
        about = update.get(KEY_ABOUT_PLAYER, "") if RECORD_PLAYER_MEMORY else ""
        if not profile and not about:
            return
        with data_lock:
            bucket = load_bucket(key)
            record = bucket.get(str(npc_id))
            if not isinstance(record, dict):
                record = {}
                bucket[str(npc_id)] = record
            old_profile = _field(record, "profile")
            old_about = _field(record, "about_player")
            if profile == old_profile and about == old_about \
                    and not update.get(KEY_NEW_FACTS):
                return
            record["name"] = npc_name
            record["updated"] = datetime.datetime.now().isoformat(timespec="seconds")
            if day is not None:
                record[DAY_KEY] = day
            if profile:
                record["profile"] = profile
            if about:
                record["about_player"] = about
            append_facts(record, update.get(KEY_NEW_FACTS) or ())
            if save_bucket(key, bucket):
                write("updated: {!r} ({}) profile {} -> {} chars, "
                      "about_player {} -> {} chars, +{} facts".format(
                          npc_name, npc_id, len(old_profile),
                          len(_field(record, "profile")), len(old_about),
                          len(_field(record, "about_player")),
                          len(update.get(KEY_NEW_FACTS) or ())))

    def player_name_of(app):
        name = getattr(getattr(app, "player", None), "name", None)
        return _text(name, 40) or "冒険者"

    def game_day(app):
        """いまのゲーム内日数。読めなければ `None`（そのときは何も言わない）。

        日付は世界に1つ（`world.days_elapsed`。GAME.md §2.16）。進めるのは
        `InstantaleApp.elapse_days` で、宿泊はここを大きく飛ばす。
        """
        if not TELL_ELAPSED_DAYS:
            return None
        value = getattr(getattr(app, "world", None), "days_elapsed", None)
        if isinstance(value, bool):
            return None
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return int(value)
        return None

    def stamp_day(key, npc_id, day):
        """最後に話したゲーム内日を控える。**同じ日なら書かない。**

        起点は「会話したこと」であって「記録が変わったこと」ではないので、
        人物像が1文字も変わらなかったターンでも控える（そうしないと
        「変更なし」が続いた分だけ経過日数が伸び続ける）。日が変わるのは
        宿泊や移動のときだけなので、書き込みは会話1回につき多くて1度。

        **控えがまだ無い相手には作らない。** 何も分かっていない相手の控えを
        ここで起こすと、LLM が居ない・抽出が落ちたときに「日付だけの控え」が
        残る。この mod の安全側の倒し方は「変更しない」なので、初回の記録は
        抽出が実って `update_record` が書くときだけにする（あちらも日を書く）。
        """
        if day is None:
            return
        with data_lock:
            bucket = load_bucket(key)
            record = bucket.get(str(npc_id))
            if not isinstance(record, dict) or record.get(DAY_KEY) == day:
                return
            record[DAY_KEY] = day
            save_bucket(key, bucket)

    def memory_block(record, player_name, today=None):
        """控えを、プロフィール欄に足す本文にする。空なら空文字。

        人物像とプレイヤーへの認識を**見出しで分ける**。1つに繋げると、
        会話の LLM が「この人物の設定」と「相手についての記憶」を混同して、
        プレイヤーの経歴を NPC 自身の経歴として話し始める。
        """
        blocks = []
        profile = _field(record, "profile")
        if profile:
            blocks.append(PROFILE_HEADING + "\n" + profile)
        about = _field(record, "about_player") if RECORD_PLAYER_MEMORY else ""
        if about:
            blocks.append(ABOUT_PLAYER_HEADING.format(player=player_name)
                          + "\n" + about)
        # 経過日数は**いちばん後ろ**に置く。人物像より後に読ませたいのは、
        # これが人物の設定ではなく「いつの話か」の但し書きだから。
        note = elapsed_note(elapsed_days(record, today), player_name)
        if note:
            blocks.append(note)
        return "\n\n".join(blocks)

    def with_profile(label, args, kwargs):
        """NPC の浅い複製の `profile` に控えを足した引数を組み直す。

        5つの会話関数は先頭4引数の並びが同じなので、ここ1つで足りる。
        複製できない・引数が短いなら**そのまま返す**。ただし
        **黙っては降りない** ― 素通りした理由がログに残らないと、注入が
        効いていないことに気付けない。
        """
        npc = kwargs.get("character_instance")
        if npc is None and len(args) >= 4:
            npc = args[3]
        if npc is None:
            note_inject("{}: no character_instance (args={}, kwargs={})".format(
                label, len(args), sorted(kwargs)))
            return args, kwargs
        app = ui.find_app()
        if app is None:
            note_inject("{}: no running app".format(label))
            return args, kwargs
        npc_id = npc_id_of(app, npc)
        if not npc_id:
            note_inject("{}: cannot name the character ({})".format(
                label, type(npc).__name__))
            return args, kwargs
        addition = memory_block(record_of(app, npc_id), player_name_of(app),
                                game_day(app))
        if not addition:
            note_inject("{}: nothing recorded for {!r} ({})".format(
                label, name_of(app, npc_id), npc_id))
            return args, kwargs
        try:
            clone = copy.copy(npc)
        except Exception as exc:
            note_inject("{}: cannot copy {} ({})".format(
                label, type(npc).__name__, type(exc).__name__))
            return args, kwargs
        base = getattr(npc, "profile", "")
        if base is None:
            base = ""
        if not isinstance(base, str):
            note_inject("{}: profile is {}".format(label, type(base).__name__))
            return args, kwargs
        clone.profile = (base.rstrip() + "\n\n" + addition
                         if base.strip() else addition)
        note_inject("{}: {!r} ({}) +{} chars into profile".format(
            label, name_of(app, npc_id), npc_id, len(addition)))
        if "character_instance" in kwargs:
            merged = dict(kwargs)
            merged["character_instance"] = clone
            return args, merged
        merged = list(args)
        merged[3] = clone
        return tuple(merged), kwargs

    def inject(orig, label, args, kwargs):
        """引数を組み直してから元の関数へ。**元の関数は必ず1回だけ呼ぶ。**"""
        try:
            args, kwargs = with_profile(label, args, kwargs)
        except Exception:
            ctx.log_exc("npc profile: injection failed")
        return orig(*args, **kwargs)

    # ------------------------------------------------------------ LLM 呼び出し
    def ask(manager_name, messages):
        """`send_request_with_no_structure` を1回。`str` が返る（GAME.md §2.12）。

        **送信モジュールを名指ししない。** 以前はローカルの `llama_cpp` と
        `any_server` の2つだけを見ていたが、Gemini / OpenAI / Claude では
        どちらも載らないので毎回空振りしていた。`llm.ask` が `llm_manager` の
        別名から引く（TECH.md §5.3）。

        `timeout` も必ず渡す。抽出は1本のワーカーで直列に回しているので、
        1回返らないと以後の抽出が全部止まる（下の `ask_structured` と同じ）。
        """
        # 出力上限は INJECT_CHARS に比例。日本語は1字≒1〜2tok なので ×3。
        return llm.ask(ctx, manager_name, messages, timeout=EXTRACT_TIMEOUT,
                       max_tokens=INJECT_CHARS * 3, label="npc profile",
                       write=write)

    # -- 構造化出力（使えるときだけ）---------------------------------------
    # grammar が JSON の形をトークン単位で強制するので、頼み文だけで JSON を
    # 書かせるより確実。**使えなければ黙って上の `ask` に降りる。**
    # 呼び方（プロバイダを名指ししない・`timeout` を必ず渡す・返却を辞書に均す）は
    # ローダに集約してある（`llm.ask`。`313_` / `902_` と共有。TECH.md §5.3）。
    def ask_structured(manager_name, messages):
        """構造化出力で1回。使えない・読めないなら `None`（呼び側が降りる）。

        返却の型に **`Literal` は使わない**（空の `Literal[]` は pydantic が
        拒否してゲームごと落ちる。`203_probe_create_model` が押さえた実際の
        落ち方）。`changed` も `bool` ではなく `str` にして、真偽の読み取りは
        `_truthy` で持つ。
        """
        import typing

        structure = llm.create_structure(
            ctx, "NpcProfileUpdate",
            {KEY_CHANGED: (str, ...),
             KEY_PROFILE: (str, ...),
             KEY_ABOUT_PLAYER: (str, ...),
             KEY_NEW_FACTS: (typing.List[str], ...)},
            label="npc profile")
        if structure is None:
            return None
        return llm.ask(ctx, manager_name, messages, timeout=EXTRACT_TIMEOUT,
                       structure=structure, max_tokens=INJECT_CHARS * 3,
                       label="npc profile", write=write)

    # ------------------------------------------------------------ 抽出の材料
    def transcribe(app, npc_id):
        """いまの会話の書き起こし。ゲーム自身の整形関数を使う（`306_` と同じ）。"""
        history = getattr(app, "current_conversation_history", None)
        if not isinstance(history, list) or not history:
            return ""
        recent = history[-CONVERSATION_TURNS:]
        player = getattr(app, "player", None)
        npc = character_of(app, npc_id)
        context_manager = sys.modules.get("scripts.llm.context_manager")
        to_text = (getattr(context_manager, "conversation_history_to_text", None)
                   if context_manager else None)
        if to_text is not None and npc is not None:
            try:
                text = to_text(recent, player, npc)
                if isinstance(text, str) and text.strip():
                    return text.strip()[-CONVERSATION_CHARS:]
            except Exception as exc:
                write("conversation_history_to_text failed: {}: {}".format(
                    type(exc).__name__, exc))
        lines = []
        for turn in recent:
            if not isinstance(turn, dict):
                continue
            lines.append("{}: {}".format(turn.get("role", "?"),
                                         _text(turn.get("content"), 300)))
        return "\n".join(lines)[-CONVERSATION_CHARS:]

    def snapshot_of(app, npc_id):
        """ワーカーへ渡す不変データ。ゲームオブジェクトは持ち出さない。"""
        npc = character_of(app, npc_id)
        if npc is None:
            note_extract_skip("NPC {!r} is not in world.characters".format(npc_id))
            return None
        history = getattr(app, "current_conversation_history", None)
        if not isinstance(history, list):
            note_extract_skip("current_conversation_history is {}".format(
                type(history).__name__))
            return None
        if not history:
            note_extract_skip("current_conversation_history is empty")
            return None
        transcript = transcribe(app, npc_id)
        if not transcript:
            note_extract_skip("conversation transcript is empty for {!r}".format(
                npc_id))
            return None
        return {
            "world": world_key(app),
            "npc_id": str(npc_id),
            "npc_name": name_of(app, npc_id),
            "player_name": player_name_of(app),
            "game_profile": _text(getattr(npc, "profile", ""), 400),
            "personality": _text(getattr(npc, "personality", ""), 300),
            "job": _text(getattr(npc, "job", ""), 60),
            "transcript": transcript,
            # ゲーム内の日付もここで採る。ワーカーは `app` を触らないので、
            # 日を跨ぐ処理に必要な値は全部この写しに乗せておく。
            "day": game_day(app),
        }

    def build_messages(snapshot, record):
        """抽出の頼み文。**返すのは JSON オブジェクト1つだけ**と指示する。

        構造化出力が使える版では grammar が形を強制するが、頼み文の側でも
        同じ形を書いておく。ゲーム自身がスキーマをプロンプト本文にも埋める
        のと同じ考えで、降りた先（`send_request_with_no_structure`）でも
        同じ読み取り（`parse_result`）で済む。

        **出来事の経過は書かせない。** 会話のあらすじはゲーム自身が
        `current_log` に覚えて次の会話にも載せるので、こちらまで語り直すと
        同じ事実が1つのプロンプトに3回並ぶ（GAME.md §2.25）。この mod が
        書くのは、会話から**分かったこと**（続く性格・関係・約束）だけ。

        **判明済みの事実は差し戻す。** 人物像は毎回まるごと書き直されるので、
        見せなければ落ちた事実は戻らない（`recent_facts`）。差し戻す先は
        抽出の頼み文だけで、**会話のプロンプトは1文字も増えない**。
        """
        npc_name = snapshot["npc_name"]
        player_name = snapshot["player_name"]
        about_chars = max(1, INJECT_CHARS // ABOUT_PLAYER_RATIO)
        fields = [
            '"{}": 記録に加えるものが有れば "true"、何も無ければ "false"'.format(
                KEY_CHANGED),
            '"{}": 更新後の{}自身の人物像の全文（{}文字以内）'.format(
                KEY_PROFILE, npc_name, INJECT_CHARS),
        ]
        if RECORD_PLAYER_MEMORY:
            fields.append(
                '"{}": 更新後の、{}から見た{}についての記録の全文（{}文字以内）'
                .format(KEY_ABOUT_PLAYER, npc_name, player_name, about_chars))
        fields.append('"{}": この会話で新しく判明した事実だけの配列。'
                      '{}に有るものは入れない。無ければ []'.format(
                          KEY_NEW_FACTS, FACTS_HEADING))
        instruction = (
            "あなたは人物の記録係だ。現在の記録と新しい会話を統合し、"
            "更新後の記録を **JSON オブジェクト1つ** で出力せよ。\n\n"
            "【出力する項目】\n- {fields}\n\n"
            "【決まり】\n"
            "- JSON の前後に説明・見出し・コードフェンスを書いてはならない\n"
            "- 会話に出ていない事を推測で補ってはならない\n"
            "- 出来事の経過や会話のあらすじ（誰が何を言った・どうしたの時系列）を"
            "書いてはならない。会話そのものの記録はゲームが別に残している。"
            "書くのは会話から分かった、この先も変わらずに残ることだけ\n"
            "- 記録に加えるものが何も無ければ {changed} を \"false\" にし、"
            "他の項目には現在の記録をそのまま写す\n"
            "- 固定の分類は使わず、継続的な性格、価値観、嗜好、経歴、関係、"
            "目標、秘密、約束を自然な人物像として簡潔に統合する\n"
            "- {about} には、呼び方、抱いている感情、交わした約束、貸し借り、"
            "頼まれた用件だけを書く。何をどう話したかの経過は書かない\n"
            "- {facts}は確定済みの記録である。要約の過程でこれらが人物像から"
            "抜け落ちていたら書き戻すこと。固有名・数値・金額・約束・条件は"
            "言い換えず、そのままの形で残す\n"
            "- 重複はまとめ、既存の記録と新しい会話が矛盾するときは新しい会話を優先する\n"
            "- 書き足すのではなく要約して統合し、各項目を指定の文字数以内に収める"
        ).format(fields="\n- ".join(fields), changed=KEY_CHANGED,
                 about=KEY_ABOUT_PLAYER, facts=FACTS_HEADING)
        blocks = [
            "【{}の素性（ゲームの記録）】\n- プロフィール: {}\n- 人格: {}\n- 役割: {}"
            .format(npc_name, snapshot["game_profile"], snapshot["personality"],
                    snapshot["job"]),
            "【現在の人物像】\n{}".format(
                _field(record, "profile") or "（まだ記録が無い）"),
        ]
        if RECORD_PLAYER_MEMORY:
            blocks.append("【現在の、{}から見た{}についての記録】\n{}".format(
                npc_name, player_name,
                _field(record, "about_player") or "（まだ記録が無い）"))
        # 判明済みの事実を差し戻す（`recent_facts` の説明）。人物像は毎回
        # 書き直されるので、ここで見せないと落ちた事実は二度と戻らない。
        recalled = recent_facts(record)
        if recalled:
            blocks.append("{}\n{}".format(
                FACTS_HEADING, "\n".join("- " + fact for fact in recalled)))
        # 前の会話からの経過日数。これが無いと、日を跨いだ会話でも
        # `about_player` が「今しがた約束した」の口調で書かれる。
        gap = elapsed_days(record, snapshot.get("day"))
        if gap:
            blocks.append("【前回の会話からの経過】\n{}日が経っている。".format(gap))
        blocks.append("【新しい会話】\n{}".format(snapshot["transcript"]))
        return [
            {"role": "user", "content": instruction + "\n\n" + "\n\n".join(blocks)},
            {"role": "user", "content": "<行動: 記録を更新する>"},
        ]

    def extract(snapshot):
        record = record_for(snapshot["world"], snapshot["npc_id"],
                            snapshot["npc_name"])
        messages = build_messages(snapshot, record)
        # 頼み文を組んだ**後**に日を控える。先に控えると経過が 0 日になり、
        # 「日が飛んだこと」を抽出LLMに伝えられなくなる。
        stamp_day(snapshot["world"], snapshot["npc_id"], snapshot.get("day"))
        # 構造化出力を先に試し、使えない版では頼み文だけの JSON に降りる。
        update = normalize_update(ask_structured(MANAGER_EXTRACT, messages))
        if update is None:
            update = parse_result(ask(MANAGER_EXTRACT, messages))
        if not update:
            write("extract: nothing to change for {!r} ({})".format(
                snapshot["npc_name"], snapshot["npc_id"]))
            return
        update_record(snapshot["world"], snapshot["npc_id"],
                      snapshot["npc_name"], update, snapshot.get("day"))

    def worker_loop():
        """仕事を順番に処理する。30秒空けば再注入時の残骸を残さず終了する。"""
        while True:
            try:
                snapshot = jobs.get(timeout=30.0)
            except queue.Empty:
                with worker_lock:
                    if not jobs.empty():
                        continue
                    state["worker"] = None
                return
            try:
                extract(snapshot)
            except Exception:
                ctx.log_exc("npc profile: background extraction failed")
            finally:
                write("extract: finished {!r} ({})".format(
                    snapshot["npc_name"], snapshot["npc_id"]))
                jobs.task_done()

    def enqueue_extract(app, npc_id):
        snapshot = snapshot_of(app, npc_id)
        if snapshot is None:
            return
        with worker_lock:
            # ワーカーが（返らない推論などで）止まっている間に会話を続けても
            # 際限なく溜めない。溢れたぶんは**古い方**から捨てる。
            while jobs.qsize() >= MAX_PENDING:
                try:
                    dropped = jobs.get_nowait()
                except queue.Empty:
                    break
                jobs.task_done()
                write("extract: dropped the oldest job for {!r} ({}) "
                      "- {} already waiting".format(
                          dropped["npc_name"], dropped["npc_id"], MAX_PENDING))
            jobs.put(snapshot)
            state["last_extract_skip"] = None
            write("extract queued: {!r} ({}) {} transcript chars".format(
                snapshot["npc_name"], snapshot["npc_id"],
                len(snapshot["transcript"])))
            worker = state["worker"]
            if worker is not None and worker.is_alive():
                return
            worker = threading.Thread(
                target=worker_loop, name="instantale_mod.npc_profile", daemon=True)
            state["worker"] = worker
            worker.start()

    def schedule_extract(app):
        """返答が描画された次のフレームで抽出をキューへ渡す。

        Clock が行うのは会話データのコピーと enqueue だけ。LLM 待ちは専用
        ワーカーなので、メインスレッドも会話画面も止めない。
        """
        if app is None:
            app = ui.find_app()
        if app is None:
            note_extract_skip("no running app")
            return
        npc_id = getattr(app, "in_conversation", None)
        if not isinstance(npc_id, str) or not npc_id:
            note_extract_skip("in_conversation is {!r}".format(npc_id))
            return
        if not screen.schedule(lambda: enqueue_extract(app, npc_id)):
            note_extract_skip("could not schedule next-frame snapshot")

    # ============================================================ フック
    @ctx.wrap("__main__:ConversationPhaseManager.conversation_continued",
              required=False)
    def conversation_continued(orig, self, choice_text, *args, **kwargs):
        """1ターンが終わったところ。**ターンの結果には手を触れない。**"""
        result = orig(self, choice_text, *args, **kwargs)
        try:
            schedule_extract(getattr(self, "app", None))
        except Exception:
            ctx.log_exc("npc profile: cannot schedule extraction")
        return result

    @ctx.wrap("__main__:ConversationInQuestPhase.conversation_continued",
              required=False)
    def conversation_in_quest_continued(orig, self, choice_text, *args, **kwargs):
        """クエスト中の会話も同じ扱い（相手は同じ NPC）。"""
        result = orig(self, choice_text, *args, **kwargs)
        try:
            schedule_extract(getattr(self, "app", None))
        except Exception:
            ctx.log_exc("npc profile: cannot schedule extraction")
        return result

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

    @ctx.wrap("scripts.llm.llm_manager:conversation_starter", required=False)
    def conversation_starter(orig, *args, **kwargs):
        return inject(orig, "starter", args, kwargs)

    @ctx.wrap("scripts.llm.llm_manager:conversation_starter_in_quest",
              required=False)
    def conversation_starter_in_quest(orig, *args, **kwargs):
        return inject(orig, "starter[quest]", args, kwargs)

    ctx.log("npc profile memory: state={}/".format(state_dir))
