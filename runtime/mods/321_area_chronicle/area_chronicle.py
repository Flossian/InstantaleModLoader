# -*- coding: utf-8 -*-
"""依頼クリアの結果を、その土地の案内文（`Area.descriptions`）へ織り込む。

ゲームは「その土地で何を成したか」を既に書いている
（`area_history[エリアid]["achievements"]`。依頼クリアごとに1〜2文、
「湿地の霧は晴れ」の粒度で土地の状態変化まで入る。GAME.md §2.20）。
その全文は会話プロンプトにも毎回載っている。
**載っているのに、NPC が自発的に言及した回は 0/775**（GAME.md §2.24）。
だからこの MOD は素材の載せ方を強化しない。
土地の描写の**原文そのもの**を書き直す。

```
    依頼クリア（QuestEndManager.execute）
        │  どの依頼かは orig の前に読む（318_ と同じ）
        ├─ 別スレッドで編纂を予約（会話も帰還も重くしない。317_ 流儀）
        │      新しい achievements ＋ 現行の descriptions → LLM 1回
        │      「変化を織り込んで書き直す。正体は変えない。長さは維持」
        │             │
        │      実行中の Area.descriptions を差し替え
        │             │
        │      state/area_chronicle/<世界名>.json
        │
        └─ ロード時（World.__init__）に再適用（318_ と同型）
```

`descriptions` は `overview`（1文）と `facilities`（数文）の2枚。
`overview` は会話の【土地情報】へ毎回一字一致で描画され、
`facilities` は依頼生成の4マネージャ（`random_quest_generator` /
`quest_summarizer` / `settlement_quest_generator` / `create_settlement_detail`）
に描画される。**原文を差し替えれば読み手全員に一度で届く**。
霧が晴れたら `facilities` から霧の記述が消える、が編纂の品質目標。

## world_data.json には書かない（不可侵の原本）

`world_data.json` はゲームが書き戻さない世界の骨格で、MOD も書かない
（`app.world_dict` へ書くと世界のファイルに焼かれ、同じ世界で新しく始めた
別のキャラクタにまで乗る。GAME.md §2.9.1、`318_` が初版で踏んだ）。
実行時の `Area.descriptions` の dict は骨格側と共有されている可能性があるので、
**中身を書き換えず、新しい dict を作って属性ごと差し替える**。

差し替えはセーブに残らないので、ロードすると素の文面へ戻る。それでよい。
戻ったぶんはロードの1回で再適用するので、遊んでいる側からは連続して見える。
そのうえで **MOD を外せば世界は素のまま**になり、後始末が要らない。
素の文面の控えも要らない（原本が world_data.json に在る。戻す＝再適用をやめる）。

## 編纂の入力は「新しい功績だけ」

控え（`done`）より後に増えた achievements だけを渡す。
古い功績は前回の編纂で既に文面へ織り込まれているので、
毎回全部渡すと同じ変化が何度も適用され、頼み文も際限なく伸びる
（achievements は追記され続ける。観測最大8件、上限管理の形跡なし）。
一度に増えた分が多いときは直近 `RECENT_DEEDS` 件に絞り、絞ったことをログに書く。

編纂が走らないまま終了しても巻き戻らない。
`done` が進んでいないので、次の到着・日数経過・クリアの照合で立ち直る。

## 言及の保証は既定 OFF

差し替えだけで言及率が上がるかは実測してから決める（介入前の基準値 0/775）。
保険として、会話の第一声（`conversation_starter`）にだけ
「近況に触れてよい」の1行を話し相手の**複製**の profile へ足す設定を持つ
（注入の形は `317_` と同じ。ゲーム世界の NPC 本体には触らない）。
既定 OFF ＝ 素の挙動が既定。
"""

import copy
import datetime
import json
import queue
import sys
import threading
import time

from instantale_modloader import frames, llm, ui
from instantale_modloader.state import (world_filename, world_key,
                                        world_key_of_dict)

# ---- 設定（既定値は mod.json の "settings" と一致させること。
#      `tools/check_mods.py` が AST で突き合わせる）------------------------
RECENT_DEEDS = 5          # 1回の編纂に渡す新しい功績の上限
MENTION_IN_STARTER = False  # 会話の第一声に「近況に触れてよい」を差すか
MENTION_TEXT = "この土地の近況は【土地情報】に書かれているとおりで、あなたも見聞きしている。会話の中で自然に触れてよい。"
LOG_MATERIAL = True       # descriptions の実行時の形を1回だけログに残すか

# ---- 以下は設定にしない値 -------------------------------------------------

LOG_BASENAME = "area_chronicle.log"

#: 世界ごとの控えを置くフォルダ（`state/` の下）。
STATE_DIRNAME = "area_chronicle"

#: 自前の `manager_name`。
#: 頼み文と返答が `output_data/<世界>/<PC>/mod_area_chronicle/N.json` に残り、
#: 編纂の質（出来事の並べ直しに堕ちていないか）を後から見られる。
MANAGER_COMPILE = "mod_area_chronicle"

#: 編纂の制限時間（秒）。**キーワードで必ず渡す**（TECH.md §5.3）。
COMPILE_TIMEOUT = 120

#: 待ち行列に積んだままにする仕事の上限。溢れたら古い方を捨てる。
MAX_PENDING = 4

#: クリア直後、ゲーム側の要約（`quest_summarizer`）が achievements を
#: 書き込むのを待つ長さと間隔（秒）。要約も LLM なので即座には現れない。
#: 待ち切れなくても仕事を捨てるだけで、`done` が進んでいないので
#: 次の到着・日数経過の照合で立ち直る。
WAIT_SECONDS = 90.0
WAIT_POLL = 3.0

#: 照合をこの秒数より短い間隔では行わない（到着・日数経過は頻繁に起きる）。
CHECK_INTERVAL = 5.0

#: `Area.descriptions` の2枚の鍵（world_data.json と同じ語彙）。
OVERVIEW_KEY = "overview"
FACILITIES_KEY = "facilities"

#: 書き直しを捨てる線の底。書き直しは「元の長さの2倍」と「この底の2倍」の
#: 大きい方まで受け、超えたら捨てて前の文面を残す
#: （切り詰めると語の途中で終わる。`317_` の二つ名と同じ判断）。
#: プロンプトは毎回全文載る世界なので、膨張は 204_（プロンプト肥大）へ直結する。
OVERVIEW_FLOOR = 100
FACILITIES_FLOOR = 300

#: 功績1件の文字列の上限。`achievements` は LLM が書いた文章なので長いことがある。
ITEM_CHARS = 400

#: 土地の名前が引けなかったときの言い方。
AREA_FALLBACK = "この土地"

#: 控え1件の鍵と、ファイルに書くときの並び（後から土地ごとに見比べるため）。
RECORD_KEYS = ("area", "name", "day", "updated", "done",
               OVERVIEW_KEY, FACILITIES_KEY)

#: ワーカーと控えの置き場所。**`apply()` の中で作ってはいけない**（TECH.md §3.4）。
#: `apply()` は再注入と遅延当て直しで何度も走る。`sys` に置けば
#: 世代をまたいで同じ1組を共有できる（`317_` / `311_` と同じ手）。
STATE_STORE_ATTR = "__instantale_area_chronicle_store__"


# --------------------------------------------------------------------- 素の関数
# `apply()` の外に出してあるものは、ゲームも `ctx` も要らない部分。
# `tools/tests/test_area_chronicle.py` がここを直接呼ぶ。

def achievements_of(record):
    """その土地で成した事の文章。読めなければ空のリスト。

    中身が文字列以外で入っていても落とさずに文字列へ均す。
    ゲームが書いている側なので形を決めつけない（GAME.md §2.7）。
    """
    if not isinstance(record, dict):
        return []
    value = record.get("achievements")
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple)):
        return []
    items = []
    for item in value:
        text = item if isinstance(item, str) else frames.repr_value(item)
        text = frames.short(text.strip(), ITEM_CHARS)
        if text:
            items.append(text)
    return items


def clean_text(text):
    """案内文を注入できる形に均す。行ごとに空白を畳み、空行を落とす。"""
    if not isinstance(text, str):
        return ""
    lines = (" ".join(line.split()) for line in text.splitlines())
    return "\n".join(line for line in lines if line).strip()


def acceptable(new, old, floor):
    """書き直しを受けるか。空と、元の2倍（底あり）を超える膨張は受けない。"""
    if not new:
        return False
    return len(new) <= 2 * max(len(old), floor)


def strip_code_fence(text):
    """``` で囲まれた返答から中身だけを取り出す。囲みが無ければそのまま。"""
    body = text.strip()
    if not body.startswith("```"):
        return body
    lines = body.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip().startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines).strip()


def parse_result(raw):
    """編纂の返答を `{"overview": 文, "facilities": 文}` に直す。読めなければ `None`。

    ゲームの構造化出力の経路を使わない理由は `317_` と同じ
    （引数の形を間違えると呼び出しが永久に返らないうえ、
    ローカルのモデルは `json_schema` を黙って守らないことがある。GAME.md §2.12）。
    2欄あるので、`317_` の評判文と違って素の文章の受け皿は持たない
    （どちらの欄か決められない文章を勝手に振り分けない）。
    片方の欄しか無い返答は、在る方だけを受ける（呼び側が欠けを前の文面で埋める）。
    """
    if isinstance(raw, dict):
        data = raw
    elif isinstance(raw, str):
        body = strip_code_fence(raw)
        start, end = body.find("{"), body.rfind("}")
        data = None
        if 0 <= start < end:
            try:
                data = json.loads(body[start:end + 1])
            except Exception:
                data = None
        if not isinstance(data, dict):
            return None
    else:
        return None
    overview = clean_text(data.get(OVERVIEW_KEY))
    facilities = clean_text(data.get(FACILITIES_KEY))
    if not overview and not facilities:
        return None
    return {OVERVIEW_KEY: overview, FACILITIES_KEY: facilities}


def ordered_record(record):
    """控え1件を `RECORD_KEYS` の並びに直す。知らない鍵は落とさず後ろへ回す。"""
    if not isinstance(record, dict):
        return {}
    out = {key: record[key] for key in RECORD_KEYS if key in record}
    for key, value in record.items():
        if key not in out:
            out[key] = value
    return out


def bullet_list(items, empty="（記録なし）"):
    return "\n".join("- " + item for item in items) if items else empty


def build_messages(item):
    """編纂の頼み文。**出来事を並べ直させない**（`317_` と同じ縛り）。

    achievements の素の文章は会話プロンプトに毎回載っている（GAME.md §2.24）。
    ここで出来事を書き足させると同じ事実が二重に載るので、
    頼むのは「変化を織り込んだ案内文の書き直し」の1点に絞る。
    """
    area = item.get("area_name") or AREA_FALLBACK
    overview = item.get(OVERVIEW_KEY) or ""
    facilities = item.get(FACILITIES_KEY) or ""
    over_limit = max(len(overview), OVERVIEW_FLOOR)
    faci_limit = max(len(facilities), FACILITIES_FLOOR)
    body = [
        "あなたは「{}」という土地の案内文を預かる書記である。".format(area),
        "以下は現在の案内文と、この土地で新しく起きた出来事の記録である。",
        "",
        "【現在の案内文: 概要】",
        overview or "（無し）",
        "",
        "【現在の案内文: 土地の様子】",
        facilities or "（無し）",
        "",
        "【新しく起きた出来事】",
        bullet_list(item.get("deeds") or []),
        "",
        "出来事の結果を織り込んで、案内文を2つとも書き直せ。",
        "守ること:",
        "- 起きた変化を織り込む。解決した問題の記述は消すか、和らげる",
        "- 土地の正体は変えない。名前・地形・施設の種類はそのまま",
        "- 出来事そのものを並べ直さない。その結果としていまの土地がどう在るかを書く",
        "- 記録に無いことを足さない",
        "- 長さは維持する。概要は{}字以内、土地の様子は{}字以内".format(
            over_limit, faci_limit),
        "",
        "返答は次の形の JSON オブジェクト1個だけとし、他には何も書かない。",
        '{"overview": "書き直した概要", "facilities": "書き直した土地の様子"}',
    ]
    return [{"role": "user", "content": "\n".join(body)}]


# --------------------------------------------------------------------------
def apply(ctx):
    store = getattr(sys, STATE_STORE_ATTR, None)
    if store is None:
        store = {
            "state": {
                "noted": set(),        # 1回だけ出す知らせの鍵
                "last_inject": None,   # 同じ結末が続く間はログに書かない
                "last_check": 0.0,     # 照合した時刻（間引き用）
                "worker": None,
                "pending": set(),      # 編纂を待っている (世界, エリア)
            },
            "cache": {"buckets": {}},  # 世界名 -> 控え（書くのはこの MOD だけ）
            "jobs": queue.Queue(),
            "data_lock": threading.RLock(),
            "worker_lock": threading.Lock(),
        }
        setattr(sys, STATE_STORE_ATTR, store)
    state = store["state"]
    cache = store["cache"]
    jobs = store["jobs"]
    data_lock = store["data_lock"]
    worker_lock = store["worker_lock"]

    write = ctx.logger(LOG_BASENAME)

    def note_once(key, message):
        if key in state["noted"]:
            return
        state["noted"].add(key)
        write(message)

    def note_inject(message):
        """注入の結末。同じ結末が続く間は書かない（会話は1ターンに何度も回る）。"""
        if state["last_inject"] == message:
            return
        state["last_inject"] = message
        write(message)

    # ------------------------------------------------------------------ 控え
    def path_for(key):
        # フォルダを作るのはここ（`ctx.state_path` が親を作る）。
        # `apply()` では作らない。一度も編纂していない `state/` に
        # 空のフォルダを置かないため（TECH.md §3.11）。
        return ctx.state_path(STATE_DIRNAME, world_filename(key))

    def bucket_of(key):
        """その世界の控え `{エリアid: 記録}`。無ければ読み込む。錠は呼び側が持つ。"""
        found = cache["buckets"].get(key)
        if found is None:
            data = ctx.read_json(path_for(key), {})
            found = data if isinstance(data, dict) else {}
            cache["buckets"][key] = found
        return found

    def record_of(key, area_id):
        with data_lock:
            record = bucket_of(key).get(str(area_id))
            return dict(record) if isinstance(record, dict) else None

    def done_of(key, area_id):
        record = record_of(key, area_id)
        value = record.get("done") if record else None
        return value if isinstance(value, int) and not isinstance(value, bool) else 0

    def save_record(key, area_id, record):
        with data_lock:
            bucket = dict(bucket_of(key))
            bucket[str(area_id)] = ordered_record(record)
            cache["buckets"][key] = {aid: bucket[aid]
                                     for aid in sorted(bucket, key=ui.id_sort_key)}
            if not ctx.write_json(path_for(key), cache["buckets"][key]):
                write("控えを書けなかった: {}".format(path_for(key)))
                return False
            return True

    # ------------------------------------------------------------ ゲームを読む
    def area_of_quest(quest):
        value = ui.quest_value(quest, "neighboring_settlement_id", None)
        return str(value) if value is not None else None

    def day_of(app):
        value = getattr(getattr(app, "world", None), "days_elapsed", None)
        return value if isinstance(value, int) and not isinstance(value, bool) else None

    def descriptions_of(area):
        """実行時の `Area.descriptions`。dict でなければ `None`（触らない）。

        world_data.json では `{overview, facilities}` の dict。
        実行時に同じ形かは版で変わりうるので、決めつけずに形で見る。
        読めない形は `LOG_MATERIAL` が1回だけ記録する（追う手がかり）。
        """
        value = getattr(area, "descriptions", None)
        return value if isinstance(value, dict) else None

    def text_of(desc, key):
        value = desc.get(key) if isinstance(desc, dict) else None
        return value if isinstance(value, str) else ""

    def describe_shape(area):
        """`descriptions` が実行時にどう見えているかの1行（確認用）。"""
        desc = getattr(area, "descriptions", None)
        if not isinstance(desc, dict):
            return "descriptions: {} 型（dict でない）".format(type(desc).__name__)
        return "descriptions: 鍵 {} / overview {}字 / facilities {}字".format(
            sorted(desc), len(text_of(desc, OVERVIEW_KEY)),
            len(text_of(desc, FACILITIES_KEY)))

    def pending_deeds(app, key, area_id):
        """(いまの功績の総数, 控え済みの数)。読めなければ (0, 0)。"""
        record = ui.area_record(getattr(app, "player", None), area_id)
        return len(achievements_of(record)), done_of(key, area_id)

    # ------------------------------------------------------------ 差し替え
    def apply_texts(area, overview, facilities, why):
        """実行中の `Area.descriptions` を差し替える。差し替えたら True。

        **中身を書き換えず、新しい dict を作って属性ごと差し替える。**
        いまの dict は世界の骨格（`app.world_dict`）と共有されている可能性があり、
        あちらへ書くと `worlds/<世界名>/world_data.json` に焼かれる
        （GAME.md §2.9.1。`318_` が初版で踏んだ轍）。
        """
        desc = descriptions_of(area)
        if desc is None:
            note_once("shape:" + ui.area_id_of(area),
                      "{}: 土地 {} の {}（差し替えない）".format(
                          why, ui.area_id_of(area), describe_shape(area)))
            return False
        new = dict(desc)
        if overview:
            new[OVERVIEW_KEY] = overview
        if facilities:
            new[FACILITIES_KEY] = facilities
        try:
            area.descriptions = new
        except Exception:
            ctx.log_exc("area chronicle: descriptions を差し替えられなかった")
            return False
        return True

    def reapply_world(world, key, why):
        """控えのある土地をすべて当て直す。ロード直後と注入直後の1回に使う。"""
        areas = ui.areas_of_world(world)
        if not areas:
            return 0
        with data_lock:
            records = {aid: dict(rec) for aid, rec in bucket_of(key).items()
                       if isinstance(rec, dict)}
        applied = 0
        for area_id in sorted(records, key=ui.id_sort_key):
            area = areas.get(str(area_id))
            if area is None:
                write("{}: 土地 {} が世界に無い（当て直さない）".format(why, area_id))
                continue
            record = records[area_id]
            if apply_texts(area, clean_text(record.get(OVERVIEW_KEY)),
                           clean_text(record.get(FACILITIES_KEY)), why):
                applied += 1
        return applied

    # ------------------------------------------------------------ 予約と照合
    def enqueue(job):
        """編纂の仕事を積む。ワーカーが居なければ起こす。"""
        key = (job["world"], job["area_id"])
        with worker_lock:
            if key in state["pending"]:
                return
            while jobs.qsize() >= MAX_PENDING:
                try:
                    dropped = jobs.get_nowait()
                except queue.Empty:
                    break
                jobs.task_done()
                state["pending"].discard((dropped["world"], dropped["area_id"]))
                write("編纂: 古い仕事を捨てた（{} / {}）".format(
                    dropped["world"], dropped["area_id"]))
            state["pending"].add(key)
            jobs.put(job)
            write("編纂を予約: {} / {}（契機 {}）".format(
                job["world"], job["area_id"], job["why"]))
            worker = state["worker"]
            if worker is not None and worker.is_alive():
                return
            worker = threading.Thread(target=worker_loop,
                                      name="instantale_mod.area_chronicle",
                                      daemon=True)
            state["worker"] = worker
            worker.start()

    def check(app, why):
        """いま居る土地に、まだ織り込んでいない功績があれば編纂を予約する。

        クリアの取りこぼしの保険。編纂がゲーム終了で走らなかった回も、
        `done` が進んでいないのでここで立ち直る。**呼ばれても大半は何もしない。**
        """
        if app is None:
            return
        now = time.monotonic()
        if now - state["last_check"] < CHECK_INTERVAL:
            return
        state["last_check"] = now
        area_id = ui.area_id_of(ui.current_area(app))
        if not area_id:
            return
        if LOG_MATERIAL:
            note_once("shape", "実行時の形: 土地 {} の {}".format(
                area_id, describe_shape(ui.current_area(app))))
        key = world_key(app)
        total, done = pending_deeds(app, key, area_id)
        if total > done:
            enqueue({"world": key, "area_id": area_id, "why": why,
                     "wait": False, "queued": time.monotonic()})

    # ------------------------------------------------------------------ 編纂
    def compile_area(job):
        app = ui.find_app()
        if app is None:
            write("編纂: app が見つからない（{} / {}）".format(
                job["world"], job["area_id"]))
            return
        key, area_id = job["world"], job["area_id"]
        if world_key(app) != key:
            write("編纂: 世界が変わっていたので見送り（{} / {}）".format(key, area_id))
            return

        # クリア直後は、ゲーム側の要約が achievements を書くのを待つ。
        deadline = job["queued"] + WAIT_SECONDS if job.get("wait") else 0.0
        total, done = pending_deeds(app, key, area_id)
        while total <= done and time.monotonic() < deadline:
            time.sleep(WAIT_POLL)
            total, done = pending_deeds(app, key, area_id)
        if total <= done:
            write("編纂: {} / {} に新しい功績が現れなかった（控え済み {}件）".format(
                key, area_id, done))
            return

        record = ui.area_record(getattr(app, "player", None), area_id)
        fresh = achievements_of(record)[done:]
        keep = max(1, int(RECENT_DEEDS))
        if len(fresh) > keep:
            write("編纂: 新しい功績 {}件のうち直近{}件だけを渡す（{} / {}）".format(
                len(fresh), keep, key, area_id))
            fresh = fresh[-keep:]

        area = ui.areas_of_world(getattr(app, "world", None)).get(str(area_id))
        if area is None:
            write("編纂: 土地 {} が世界に無い".format(area_id))
            return
        desc = descriptions_of(area)
        if desc is None:
            note_once("shape:" + str(area_id),
                      "編纂: 土地 {} の {}（編纂しない）".format(
                          area_id, describe_shape(area)))
            return
        overview = clean_text(text_of(desc, OVERVIEW_KEY))
        facilities = clean_text(text_of(desc, FACILITIES_KEY))
        name = frames.short(getattr(area, "name", ""), 40) or ""

        messages = build_messages({
            "area_name": name,
            OVERVIEW_KEY: overview,
            FACILITIES_KEY: facilities,
            "deeds": fresh,
        })
        raw = llm.ask(ctx, MANAGER_COMPILE, messages, timeout=COMPILE_TIMEOUT,
                      label="area chronicle", write=write)
        parsed = parse_result(raw)
        if parsed is None:
            # 読めなかったときは**前の文面を残す**。`done` も進めないので、
            # 次の照合で同じ功績からやり直す。
            write("編纂: 読めなかったので前の案内文を残す（{} / {}）".format(
                key, area_id))
            return

        new_overview = parsed[OVERVIEW_KEY]
        if new_overview and not acceptable(new_overview, overview, OVERVIEW_FLOOR):
            write("編纂: 概要が膨らみすぎたので捨てた（{}字）".format(len(new_overview)))
            new_overview = ""
        new_facilities = parsed[FACILITIES_KEY]
        if new_facilities and not acceptable(new_facilities, facilities,
                                             FACILITIES_FLOOR):
            write("編纂: 土地の様子が膨らみすぎたので捨てた（{}字）".format(
                len(new_facilities)))
            new_facilities = ""
        if not new_overview and not new_facilities:
            write("編纂: 使える書き直しが無かった（{} / {}）".format(key, area_id))
            return

        # 欠けた欄は前の文面のまま。両方そろって初めて1つの案内文なので、
        # 控えには**当てた結果の組**を書く（次のロードでこの組を再適用する）。
        final_overview = new_overview or overview
        final_facilities = new_facilities or facilities
        if not apply_texts(area, final_overview, final_facilities, "compile"):
            return
        if save_record(key, area_id, {
                "area": str(area_id),
                "name": name,
                "day": day_of(app),
                "updated": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "done": total,
                OVERVIEW_KEY: final_overview,
                FACILITIES_KEY: final_facilities,
        }):
            write("編纂できた: {} / {} 概要={!r} 様子={}字（功績 {}件を織り込み済み）"
                  .format(key, area_id, frames.short(final_overview, 60),
                          len(final_facilities), total))

    def worker_loop():
        """仕事を順番に処理する。30秒空けば再注入時の残骸を残さず終了する。"""
        while True:
            try:
                job = jobs.get(timeout=30.0)
            except queue.Empty:
                with worker_lock:
                    if not jobs.empty():
                        continue
                    state["worker"] = None
                return
            try:
                compile_area(job)
            except Exception:
                ctx.log_exc("area chronicle: 編纂に失敗した")
            finally:
                with worker_lock:
                    state["pending"].discard((job["world"], job["area_id"]))
                jobs.task_done()

    # ---------------------------------------------------- 第一声への差し込み
    def with_mention(args, kwargs):
        """第4引数の NPC を浅く複製し、`profile` に近況の断りを足す。

        注入の位置は `317_` と同じ（浅い複製。ゲーム世界の NPC 本体は触らない）。
        足すのは**この土地の年代記が在るときだけ** ―
        何も変わっていない土地で近況を促すと、無いものへの言及を招く。
        """
        text = MENTION_TEXT.strip() if isinstance(MENTION_TEXT, str) else ""
        if not text:
            return args, kwargs
        npc = kwargs.get("character_instance")
        if npc is None and len(args) >= 4:
            npc = args[3]
        if npc is None:
            note_inject("starter: character_instance が無い（args={}, kwargs={}）"
                        .format(len(args), sorted(kwargs)))
            return args, kwargs
        app = ui.find_app()
        if app is None:
            note_inject("starter: app が見つからない")
            return args, kwargs
        area_id = ui.area_id_of(ui.current_area(app))
        if not area_id or record_of(world_key(app), area_id) is None:
            note_inject("starter: この土地の年代記はまだ無い")
            return args, kwargs
        base = getattr(npc, "profile", "") or ""
        if not isinstance(base, str):
            note_inject("starter: profile が {} なので足せない".format(
                type(base).__name__))
            return args, kwargs
        try:
            clone = copy.copy(npc)
        except Exception as exc:
            note_inject("starter: {} を複製できない（{}）".format(
                type(npc).__name__, type(exc).__name__))
            return args, kwargs
        clone.profile = (base.rstrip() + "\n\n" + text) if base.strip() else text
        note_inject("starter: profile に近況の断り{}字を足した".format(len(text)))
        if "character_instance" in kwargs:
            merged = dict(kwargs)
            merged["character_instance"] = clone
            return args, merged
        merged = list(args)
        merged[3] = clone
        return tuple(merged), kwargs

    @ctx.wrap("scripts.llm.llm_manager:conversation_starter", required=False)
    def conversation_starter(orig, *args, **kwargs):
        if MENTION_IN_STARTER:
            try:
                args, kwargs = with_mention(args, kwargs)
            except Exception:
                ctx.log_exc("area chronicle: 第一声への差し込みに失敗した")
        return orig(*args, **kwargs)

    # ------------------------------------------------------------------ フック
    @ctx.wrap("__main__:QuestEndManager.execute", required=False, safe=True)
    def quest_end(orig, self, *args, **kwargs):
        """依頼を片付けた。その依頼の土地の編纂を予約する。

        **どの依頼が終わるのかは `orig` の前に読む**
        （終わった後では `current_quest_data` が片付いている。`318_` と同じ）。
        """
        app = getattr(self, "app", None) or ui.find_app()
        area_id = None
        try:
            quest = getattr(app, "current_quest_data", None) if app is not None else None
            if quest is not None:
                area_id = area_of_quest(quest)
        except Exception:
            ctx.log_exc("area chronicle: 終わる依頼を読めなかった")
        result = orig(self, *args, **kwargs)
        try:
            if app is not None and area_id:
                enqueue({"world": world_key(app), "area_id": area_id,
                         "why": "クリア", "wait": True,
                         "queued": time.monotonic()})
            elif app is not None:
                write("clear: 終わった依頼の土地が読めなかった（編纂しない）")
        except Exception:
            ctx.log_exc("area chronicle: クリアで予約できなかった")
        return result

    @ctx.wrap("__main__:World.__init__", required=False, safe=True)
    def world_loaded(orig, self, save_data_dict, app, *args, **kwargs):
        """セーブを読み込んだ。控えのある土地の案内文を当て直す。

        差し替えはセーブに残らないので、ここが素へ戻った状態の始点になる
        （`318_` と同型）。世界の鍵はセーブの辞書から取る ―
        この時点では `app.world` がまだ埋まっていない。
        """
        result = orig(self, save_data_dict, app, *args, **kwargs)
        try:
            key = world_key_of_dict(save_data_dict, None) or world_key(app)
            if key:
                applied = reapply_world(self, key, "load")
                if applied:
                    write("load: 世界 {!r} の案内文を当て直した（{}件）".format(
                        key, applied))
        except Exception:
            ctx.log_exc("area chronicle: ロード直後に当て直せなかった")
        return result

    @ctx.wrap("__main__:AreaMoveManager.execute", required=False, safe=True)
    def area_arrival(orig, self, choice_text=None, *args, **kwargs):
        result = orig(self, choice_text, *args, **kwargs)
        try:
            check(getattr(self, "app", None) or ui.find_app(), "到着")
        except Exception:
            ctx.log_exc("area chronicle: 土地への到着で失敗した")
        return result

    @ctx.wrap("__main__:InstantaleApp.elapse_days", required=False, safe=True)
    def elapse_days(orig, self, days=None, *args, **kwargs):
        result = orig(self, days, *args, **kwargs)
        try:
            check(self, "日数経過")
        except Exception:
            ctx.log_exc("area chronicle: 日数の経過で失敗した")
        return result

    # ------------------------------------------------------------ 注入直後
    def initial_apply():
        """遊んでいる最中に注入されたときの当て直し（1回だけ）。

        ロードを通らずに世界が既に立っている場合、
        `World.__init__` のフックでは拾えないのでここで1回当てる。
        """
        try:
            app = ui.find_app()
            world = getattr(app, "world", None) if app is not None else None
            if world is None:
                return
            key = world_key(app)
            applied = reapply_world(world, key, "inject")
            if applied:
                write("inject: 実行中の世界 {!r} へ当て直した（{}件）".format(
                    key, applied))
        except Exception:
            ctx.log_exc("area chronicle: 注入直後の当て直しに失敗した")

    ctx.on_ready(initial_apply)

    ctx.log("area chronicle: installed (直近{}件, 第一声への差し込み={})".format(
        RECENT_DEEDS, MENTION_IN_STARTER))
