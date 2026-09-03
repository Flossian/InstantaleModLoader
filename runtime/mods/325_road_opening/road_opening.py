# -*- coding: utf-8 -*-
"""機能追加: 「新たな道を探す」。まだ繋がっていない街への道を、プレイヤー起点で切り開く。

素の世界の街どうしの繋がり（`Area.connections`）は、開始地点をハブに3本の一本道が
伸びる木で固定されている（0→1→2→3 / 0→4→5→6 / 0→7→8。GAME.md §2.25）。
この MOD は「他の土地へ行く」の一覧に「新たな道を探す」を1つ足す。

    [他の土地へ行く]  隣の街A / 隣の街B / 新たな道を探す / やめる
                                            ↓
                      まだ繋がっていない街の一覧（金額と道の難易度つき）
                                            ↓
                      お金を支払い委託する ─ 支払い、PAY_DAYS 日後に道が開く（移動はしない）
                      自ら切り拓く ─ 道中のクエストを作って受注。踏破すれば道が開き、
                                     そのままその街に着く。放棄すれば開かない

委託は世界ごとの控えの `commissions` に期日（ゲーム内の日付）で持ち、
日数が進んだとき（`elapse_days` の後）と画面が組み直されたときに期日を見て開く。
開通の1行は手が空いてから出す（移動の文の最中に割り込まない）。

開いた道は両側の `Area.connections` へ対称に足す。
以後は素の「他の土地へ行く」に普通に並び、徒歩でも馬車でも行ける。

## 距離補正

いまの接続の上で最短経路を辿り（BFS。開いた道も算入）、
「間に挟む街の数」を金額と難易度に加算する。

    金額     = PRICE_BASE + PRICE_PER_HOP × 挟む街の数
    難易度   = 基準（DIFFICULTY_MODE。`307_` と同じ語彙）+ DIFFICULTY_PER_HOP × 挟む街の数
               + DIFFICULTY_OFFSET

隣の隣は安く軽く、木の反対側の枝の先端へは高く重くなる。
移動網から孤立した街（エディタで足した直後の街。BFS が届かない）は
挟む街の数が定義できないので `ISOLATED_HOPS`（既定 5 ＝ 9街の木の最遠と同じ）を使う。
この場合「新たな道を探す」がその街への唯一の正規の導入経路になる。

## 街の見分けは `size`

id 0〜8 の決め打ちではなく `size` が village / town / city のエリアを街とみなす
（冒険エリアは dungeon。実データで確認。HANDOVER_world_structure.md §3）。
エディタで後から足した街も自動で候補になる。

**実行時の `Area.size` は読めない**（`207_` と `324_` の実機で取れず。GAME.md §2.7）。
`324_` の `size_of` と同じ順で、実行時の属性 → `app.save_data_dict["areas"][id]["size"]`
→ `app.world_dict["areas"][id]["size"]` と落ちる。初版は属性しか見ておらず、
実機で候補が 0 件のまま「新たな道を探す」が出なかった（2026-09-03）。

## world_data.json には書かない（不可侵の原本）

接続の変更は実行時の `Area.connections` だけ。
`world_dict` に書くと世界のファイルに焼かれ、同じ世界で新しく始めた別のキャラクタにまで乗る
（`318_` が初版で踏んだ。GAME.md §2.9.1）。
実行時の list が骨格側と共有されている可能性があるので、**中身を書き換えず、
新しい list を作って属性ごと差し替える**（`321_` の descriptions と同じ）。

差し替えはセーブに残らないので、ロードすると素の接続へ戻る。それでよい。
開通の記録は `state\\road_opening\\<世界名>.json` に持ち、
ロードの1回（`World.__init__`）で当て直す（`318_` / `321_` と同型）。
**MOD を外せば世界は素のまま**になり、後始末が要らない。

## ダンジョン踏破は `307_` と同じ機構

「2地点間の道中クエスト生成 → 踏破で目的地に到着 → 放棄で出発地に留まる」は
`307_area_move_dungeon` が持っている（体力の門・依頼概要への行き先明記・紐付けの解除まで）。
この MOD は同じ機構を自前で持つ（`307_` を入れていない環境でも動く。番号で import しない。
TECH.md §3.2.3）。違いは3点:

1. 対象が非接続の街であること（確認画面が無いので、ゲームのボタンの `args` を写せない。
   到着の移動は実測の `mode`（`on_foot`。GAME.md §2.18）で組み、組めなければ
   その街の確認画面（`AreaMoveCofirmation`）を開いて手段を選ばせる）
2. 難易度に距離補正が乗ること
3. 完了時に接続を書くこと

到着の移動にかかる日数は `ARRIVAL_DAYS`（既定 14 ＝ 馬車と同じ。`307_` の `TRAVEL_DAYS` と同じ理由）。
道中のクエストは日数を進めない（GAME.md §2.18）。

## 状態はゲーム自身に聞く

道中の控えは世界ごとの控えの `pending` に持ち、段階で進める:

    offered   道を選んでクエストを作った（まだ受注していない）
    armed     ゲームがそのクエストを進めている
    ready     踏破した。道は開いた。移動を起こしてよい
    arriving  移動を起こす予約をした
    moving    移動を渡した。着いたか、期限が来たら外す

`QuestStartManager.__init__` の一瞬に頼らず、`app.current_quest_data` の id で判定する
（`307_` の教訓）。

## 自前のクラス名を `PhaseSpec` に書かない

ボタンはセーブに焼かれうる。無害な既存クラスを持たせ、押下は `on_button_press` を包んで
ボタン辞書の印で横取りする（印のキーは `mod_road_opening`。他の MOD と別）。

遊び方の説明は DOC.md、検証の物差しは VERIFICATION.md §3.50。
"""

import datetime
import random
import sys
import time

from instantale_modloader import ui
from instantale_modloader.state import (UNKNOWN_WORLD, WorldStore, world_key,
                                        world_key_of_dict)


LOG_BASENAME = "road_opening.log"

#: 世界ごとの控え `state/road_opening/<世界名>.json`。
#: 開いた道（`roads`）と、進行中の道中（`pending`）。
STATE_DIRNAME = "road_opening"

#: 控えの置き場（`sys` の属性名）。注入し直しをまたいで残す。
STATE_STORE_ATTR = "__instantale_road_opening_store__"

#: 押下を横取りするための印。他の MOD と別のキーにすること。
MARK = "mod_road_opening"

#: ボタン辞書に載せる対象の街の id。`mod_` で始めるのは、
#: 他の MOD の掃除（`marked_by_a_mod`）に残骸と見なされないため。
TARGET_KEY = "mod_road_opening_target"

# ---------------------------------------------------------------- 設定（mod.json）
# ここの定数だけが GUI から変えられる（ローダは入口モジュールのグローバルへ書き込む。
# TECH.md §3.8）。
#
# 「他の土地へ行く」の一覧に足すボタンの文字列。
SEARCH_LABEL = "新たな道を探す"

# お金で開くときの基本額と、挟む街1つあたりの加算額。
# 馬車の運賃が 1000G（実測。GAME.md §2.18）。道は一度開けば以後ずっと使えるので、
# 隣の隣（挟む街1つ）で馬車3回ぶんになる値にしてある。
PRICE_BASE = 2000
PRICE_PER_HOP = 1000

# 道の難易度の基準の決め方（`307_` と同じ語彙）。
#   "between"     現在地と対象の間から一様に選ぶ（既定）
#   "destination" 対象に合わせる
#   "harder"      高いほう
# 対象の街の適正が読めない（未訪問で依頼が無い）ときは、読めた側だけで決める。
DIFFICULTY_MODE = "between"

# 挟む街1つあたりの難易度の下駄。
DIFFICULTY_PER_HOP = 2

# 選んだ難易度に足す下駄（距離とは無関係に一律）。
DIFFICULTY_OFFSET = 0

# 移動網から孤立した街（BFS が届かない）に使う「挟む街の数」。
# 既定 5 ＝ 9街の木で最も遠い2つの街（枝の先端どうし）の間に挟まる数。
ISOLATED_HOPS = 5

# 踏破した後の到着の移動にかかる日数。上限ではなく、この日数になる。
# 既定 14 は馬車と同じ（`307_` の `TRAVEL_DAYS` と同じ判断）。0 なら進めない。
ARRIVAL_DAYS = 14

# 体力（スタミナ）がこの割合を下回っていたら踏破の道を断る。0 で見ない（`307_` と同じ）。
STAMINA_MIN_PERCENT = 33

# 開通のときに出す1行。空で出さない。使える変数: {a} {b}（両側の街の名前）。
OPEN_TEXT = "{a}と{b}を結ぶ道が開かれた。"

# お金で開いたとき `area_history.achievements` に書く1件。空で書かない。変数は同じ。
# 踏破のときは書かない（依頼クリアそのものをゲームが功績に書く）。
ACHIEVEMENT_TEXT = "{a}と{b}を結ぶ新しい道が開かれた。"

# 到着の移動中、ゲームが出す「徒歩で目指す。長旅だ...」を伏せるか。
HIDE_TRAVEL_TEXT = True

# 街とみなす `Area.size` の値。カンマ区切り。
TOWN_SIZES = "village,town,city"

# 候補の1行の表示。使える変数: {name} {price} {difficulty} {hops}。
CANDIDATE_LABEL = "{name}（{price}G／難易度 {difficulty}）"

# 手段を選ぶ画面の2つのボタン。
# 委託: 使える変数 {price} {days}。切り拓く: {difficulty}。
PAY_LABEL = "お金を支払い委託する（{price}G／{days}日）"
DUNGEON_LABEL = "自ら切り拓く（難易度 {difficulty}）"

# 委託してから開通するまでの日数。0 で支払った場で開通する。
# 期日はゲーム内の日付（`world.days_elapsed`）で数え、日数が進んだときに開く。
PAY_DAYS = 14

# 委託したときに出す1行。空で出さない。使える変数: {price} {target} {days}。
COMMISSION_TEXT = "{price}G を支払い、{target}への道の開削を委託した。開通まで {days}日。"

# ---------------------------------------------------------------- コード側の設定
#: 日数が 0 のときの委託ボタン（{days} を出さない）。
PAY_LABEL_NOW = "お金を支払い委託する（{price}G）"
BACK_LABEL = "やめる"

#: セーブから復元された残骸を見分けるための、こちらのラベルの前方一致（`prune_stale`）。
#: 汎用語（「やめる」）は入れない。
OUR_LABEL_PREFIXES = (SEARCH_LABEL, "お金を支払い委託する（", "自ら切り拓く（")

#: 行き先一覧の1行の spec のクラス名と、`args` の並び `[target_area_id]`
#: （`133_` の実測。GAME.md §2.18）。
TOWN_CLS = "AreaMoveCofirmation"

#: 到着の移動に使う `AreaMoveManager` の `mode`。**実機で観測した値**（GAME.md §2.18）。
#: 確認画面が無い相手なのでゲームのボタンから写せない。組めなければ確認画面へ落とす。
WALK_MODE = "on_foot"

#: `QuestChoiceManager(app, quest_type, quest_id)` の `quest_type`。
#: `world.quests` に対して通るのはこれだけ（GAME.md §2.9）。
QUEST_TYPE = "settlement_quest"

#: 依頼の難易度をその土地に結ぶ鍵（ゲーム自身の関数が無いときの落とし所。GAME.md §2.9）。
QUEST_AREA_KEY = "neighboring_settlement_id"
QUEST_DIFFICULTY_KEY = "difficulty"

#: 到着の移動中だけ伏せる文言（`307_` と同じ実測値）。
MUTED_MOVE_TEXTS = ("徒歩で目指す", "長旅だ", "馬車で目指す")

#: 集落の画面と見なす spec のクラス名（`307_` と同じ）。
SETTLEMENT_MARKS = ("MovePhaseManager", "DisplayTalkChoice", "DisplayAreaMoveChoice")

#: 画面に出す文言。
NO_CANDIDATE_TEXT = "（ここから新たに繋げられる街は無い）"
NO_GOLD_TEXT = "（{price}G に足りない。手持ち {gold}G）"
ALREADY_TEXT = "（{target}への道はもう開いている）"
PAID_TEXT = "{price}G を支払い、{target}への道を拓いた。"
ALREADY_COMMISSIONED_TEXT = "（{target}への道は開削を委託済み。開通まであと {days}日）"
REFUSE_TEXT = "この道を行くには、今は体力が無い。"
REFUSE_DETAIL = "（体力 {value}/{limit}。休むか、医者にかかるかだ）"
LOOKING_TEXT = "{target}へ抜ける道の話を聞いている……"
FOUND_TEXT = "「{title}」。{target}へ抜ける道は、そう呼ばれている。"
DANGER_TEXT = "（この道のりの危険度: {difficulty} ／ 踏破すれば{target}への道が開く）"
ARRIVE_TEXT = "道は抜けた。{target}が見えてくる。"
RETIRE_TEXT = "道を引き返した。{origin}と{target}を結ぶ道は開かれなかった。"
NO_ROAD_TEXT = "（その道は今は見つからない）"
NO_QUEST_TEXT = "（道の話はまとまらなかった）"
ARRIVAL_NOTE = "\n\n※このクエストをクリアすると「{origin}」と「{target}」を結ぶ道が開き、「{target}」に移動します。"
ARRIVAL_NOTE_MARK = "※このクエストをクリアすると"

#: 生成の印の寿命（秒）と、控えの寿命（秒）、最後の移動を外す期限（秒）。`307_` と同じ。
INJECT_TTL = 300.0
PENDING_TTL = 7 * 24 * 3600.0
MOVE_TIMEOUT = 300.0
SETTLE = 0.4

#: 難易度の下限。上は抑えない。
MIN_DIFFICULTY = 1

#: 乱数は MOD 専用（TECH.md §6.1）。
_RNG = random.Random()


# ================================================================ 純粋な部品
class _SafeDict(dict):
    def __missing__(self, key):
        return "{" + str(key) + "}"


def fmt(template, **values):
    """設定のテンプレートを埋める。壊れたテンプレートでも素の文字列で返す。"""
    try:
        return str(template).format_map(_SafeDict(values))
    except Exception:
        return str(template)


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def parse_sizes(text):
    """`TOWN_SIZES` を集合にする。"""
    if not isinstance(text, str):
        return set()
    return {part.strip().lower() for part in text.split(",") if part.strip()}


def size_of(app, area, area_id=None):
    """土地の種類。実行時の属性 → セーブの辞書 → 骨格の辞書 の順（`324_` の `size_of` と同じ）。

    実行時の `Area.size` は読めない（GAME.md §2.7）ので、実際に効くのは辞書の側。
    読めなければ None。
    """
    size = getattr(area, "size", None)
    if isinstance(size, str) and size.strip():
        return size.strip().lower()
    wanted = str(area_id if area_id is not None else ui.area_id_of(area))
    for attr in ("save_data_dict", "world_dict"):
        container = getattr(app, attr, None)
        areas = container.get("areas") if isinstance(container, dict) else None
        if not isinstance(areas, dict) or not wanted:
            continue
        entry = areas.get(wanted)
        if entry is None:
            for key, value in areas.items():
                if str(key) == wanted:
                    entry = value
                    break
        size = entry.get("size") if isinstance(entry, dict) else None
        if isinstance(size, str) and size.strip():
            return size.strip().lower()
    return None


def is_town(app, area, sizes, area_id=None):
    size = size_of(app, area, area_id)
    return size is not None and size in sizes


def connections_of(area):
    """その土地の接続先 id を文字列の一覧で。無ければ空。"""
    value = getattr(area, "connections", None)
    if not isinstance(value, (list, tuple)):
        return []
    return [str(v) for v in value]


def build_graph(areas):
    """`{id: set(id)}` の無向グラフ。片方向しか無い辺も両方向に張る。"""
    graph = {}
    for area_id, area in areas.items():
        key = str(area_id)
        graph.setdefault(key, set())
        for other in connections_of(area):
            graph[key].add(other)
            graph.setdefault(other, set()).add(key)
    return graph


def hops_between(graph, origin, target):
    """最短経路で間に挟む街の数。届かなければ None。同じ街なら None。"""
    origin, target = str(origin), str(target)
    if origin == target:
        return None
    seen = {origin}
    frontier = [origin]
    edges = 0
    while frontier:
        edges += 1
        following = []
        for node in frontier:
            for other in sorted(graph.get(node, ())):
                if other in seen:
                    continue
                if other == target:
                    return edges - 1
                seen.add(other)
                following.append(other)
        frontier = following
    return None


def price_for(hops):
    return max(0, int(PRICE_BASE) + int(PRICE_PER_HOP) * max(0, int(hops)))


def difficulty_for(origin_level, target_level, hops, rng=_RNG):
    """基準（`DIFFICULTY_MODE`）に距離の下駄と一律の下駄を足す。

    読めない側（None）は除いて決める。両方読めなければ下限から。
    """
    known = [int(v) for v in (origin_level, target_level) if _number(v)]
    if not known:
        base = MIN_DIFFICULTY
    elif DIFFICULTY_MODE == "destination":
        base = int(target_level) if _number(target_level) else known[0]
    elif DIFFICULTY_MODE == "harder":
        base = max(known)
    else:
        low, high = min(known), max(known)
        base = rng.randint(low, high)
    value = base + int(DIFFICULTY_PER_HOP) * max(0, int(hops)) + int(DIFFICULTY_OFFSET)
    return max(MIN_DIFFICULTY, value)


def add_connection(area, other_id):
    """`connections` に相手を足す。足したら True。**新しい list を作って属性ごと差し替える。**"""
    current = connections_of(area)
    other_id = str(other_id)
    if other_id in current:
        return False
    old = getattr(area, "connections", None)
    if isinstance(old, (list, tuple)) and old and not isinstance(old[0], str):
        # 既存の要素の型に合わせる（実データは文字列だが、決めつけない）。
        try:
            merged = list(old) + [type(old[0])(other_id)]
        except Exception:
            merged = list(old) + [other_id]
    else:
        merged = list(old or []) + [other_id]
    area.connections = merged
    return True


def road_brief(origin_name, target_name, difficulty):
    """生成プロンプトの `area_description` に足す文（`307_` と同じ手口）。"""
    origin = origin_name or "出発地"
    target = target_name or "目的地"
    return (
        "\n\n【この依頼の性質。最優先で反映すること】\n"
        "これは町で受ける雑事ではなく、**「{origin}」から「{target}」へ通じる"
        "新しい道を切り開く旅路そのもの**である。まだ誰も通ったことのない道である。\n"
        "- 舞台は2つの土地を繋ぐ未踏の道中（峠・古い地下道・森・湿地・廃道など）"
        "とすること。町の中を舞台にしてはならない。\n"
        "- area は、その道中を進んでいく一本道として設計すること。\n"
        "- quest_title は「{origin}」から「{target}」への道を拓くことが分かるもの"
        "とすること。\n"
        "- request_summary は「道を切り開いて『{target}』へ到達する」ことを"
        "目的として書くこと。\n"
        "- boss は、その道を塞いでいる存在（主・群れの長・崩落を招いた何か）"
        "とすること。討ち払えば道が通れるようになる、という筋にすること。\n"
        "- client_statement は、新しい道を望む（あるいは恐れる）土地の者の言葉"
        "とすること。\n"
        "- この道のりの危険度は {difficulty} である。それに見合う相手を配置すること。"
    ).format(origin=origin, target=target, difficulty=difficulty)


def ordered_bucket(bucket):
    """控えを書く前に並びを固定する（`state/` の差分を読めるように）。"""
    if not isinstance(bucket, dict):
        return {"roads": [], "commissions": [], "pending": None}
    out = {"roads": list(bucket.get("roads") or []),
           "commissions": list(bucket.get("commissions") or []),
           "pending": bucket.get("pending")}
    for key, value in bucket.items():
        if key not in out:
            out[key] = value
    return out


def describe_pending(record):
    if not isinstance(record, dict):
        return "none"
    return "{}:{}->{}".format(record.get("stage"), record.get("quest_id"),
                              record.get("target_name"))


# ================================================================ 本体
def apply(ctx):
    store = getattr(sys, STATE_STORE_ATTR, None)
    if not isinstance(store, dict):
        store = {
            "worlds": WorldStore(ctx, STATE_DIRNAME, order=ordered_bucket),
            "state": {
                "inject": None,      # random_quest_generator への差し込み（1回で使い切る）
                "inject_at": 0.0,
                "generating": False,
                "departing": False,  # 自分が起こした AreaMoveManager かどうかの印
                # 自前のフェーズが動いている間の旗。押下はここが立っている間は無視する。
                # 押してから次のフレームで選択肢が差し替わるまでの間に同じボタンをもう一度
                # 押せてしまい、支払いが二重に起きた（実機 2026-09-03。VERIFICATION.md §3.50）。
                "acting": False,
                "scan_warned": False,
                "unsized_noted": False,
            },
        }
        setattr(sys, STATE_STORE_ATTR, store)
    state = store["state"]

    log_path = ctx.out_path(LOG_BASENAME)
    write = ctx.logger(LOG_BASENAME)
    worlds = store["worlds"].rebind(ctx, write)
    screen = ui.Screen(ctx, write, tag="road opening", mark=MARK)

    # ------------------------------------------------------------ 控え
    def bucket_of(key):
        bucket = worlds.load(key)
        bucket.setdefault("roads", [])
        bucket.setdefault("pending", None)
        return bucket

    def roads_of(key):
        return [r for r in bucket_of(key).get("roads") or [] if isinstance(r, dict)]

    def pending_of(app, *stages):
        """いま有効な道中の控え。段階が合わなければ None。`moving` は期限を見る。"""
        if app is None:
            return None
        key = world_key(app)
        bucket = bucket_of(key)
        record = bucket.get("pending")
        if not isinstance(record, dict):
            return None
        if time.time() - float(record.get("at") or 0) > PENDING_TTL:
            write("pending: dropped a stale record ({})".format(describe_pending(record)))
            bucket["pending"] = None
            worlds.save(key)
            return None
        if record.get("stage") == "moving" and \
                time.time() - float(record.get("moving_at") or 0) > MOVE_TIMEOUT:
            drop_pending(app, "the move did not finish in {:.0f}s".format(MOVE_TIMEOUT))
            return None
        if stages and record.get("stage") not in stages:
            return None
        return record

    def set_pending(app, record):
        key = world_key(app)
        bucket_of(key)["pending"] = record
        worlds.save(key)

    def advance(app, stage, **fields):
        record = pending_of(app)
        if record is None:
            return None
        record["stage"] = stage
        record.update(fields)
        worlds.save(world_key(app))
        return record

    def drop_pending(app, why, clear_note=True):
        """道中の紐付けを外す。依頼概要に足した一文も消す（`307_` と同じ理由）。"""
        key = world_key(app)
        bucket = bucket_of(key)
        record = bucket.get("pending")
        if not isinstance(record, dict):
            return False
        if clear_note and record.get("quest_id"):
            removed = strip_quest_value(app, str(record["quest_id"]), "request_summary",
                                        ARRIVAL_NOTE_MARK)
            if removed:
                write("note: removed the arrival line from quest {!r} ({} store(s))"
                      .format(record["quest_id"], removed))
        write("pending: dropped ({}) {}".format(why, describe_pending(record)))
        bucket["pending"] = None
        worlds.save(key)
        return True

    # ------------------------------------------------------------ 依頼の読み書き
    def strip_quest_value(app, quest_id, name, mark):
        written = 0
        for qstore in ui.quest_stores(app):
            target = qstore.get(quest_id)
            if target is None:
                continue
            current = ui.quest_value(target, name, "")
            if not isinstance(current, str) or mark not in current:
                continue
            trimmed = current[:current.rfind(mark)].rstrip()
            try:
                if isinstance(target, dict):
                    target[name] = trimmed
                else:
                    setattr(target, name, trimmed)
                written += 1
            except Exception:
                write("WARN note: cannot strip {} on {!r}".format(name, quest_id))
        return written

    def append_quest_value(app, quest_id, name, suffix, mark):
        written = 0
        for qstore in ui.quest_stores(app):
            target = qstore.get(quest_id)
            if target is None:
                continue
            current = ui.quest_value(target, name, "") or ""
            if not isinstance(current, str) or (mark and mark in current):
                continue
            try:
                if isinstance(target, dict):
                    target[name] = current + suffix
                else:
                    setattr(target, name, current + suffix)
                written += 1
            except Exception:
                write("WARN note: cannot append to {} on {!r}".format(name, quest_id))
        return written

    def write_difficulty(app, quest_id, difficulty, when):
        """選んだ難易度を両方の格納先へ書く（`307_` と同じ。受注の時点でもう一度書く）。"""
        written = ui.set_quest_value(
            app, str(quest_id), QUEST_DIFFICULTY_KEY, int(difficulty),
            on_error=lambda msg: write("WARN difficulty: " + msg))
        write("{}: difficulty {} written to {} store(s) for quest {!r}".format(
            when, difficulty, written, quest_id))
        return written

    def note_arrival(app, quest_id, origin_name, target_name):
        written = append_quest_value(
            app, str(quest_id), "request_summary",
            ARRIVAL_NOTE.format(origin=origin_name, target=target_name),
            ARRIVAL_NOTE_MARK)
        if written:
            write("note: added the arrival line to request_summary ({} store(s))"
                  .format(written))
        return written

    # ------------------------------------------------------------ 土地の読み
    def area_name(area, fallback):
        name = getattr(area, "name", None)
        return name if isinstance(name, str) and name else fallback

    def difficulties_of(app, area):
        """その土地の依頼の難易度の一覧。ゲーム自身の関数に聞く（`133_` と同じ）。"""
        world = getattr(app, "world", None)
        functions = sys.modules.get("scripts.functions")
        fn = getattr(functions, "get_quest_difficulties", None) if functions else None
        if fn is not None:
            try:
                return [v for v in (fn(area, world) or []) if _number(v)]
            except Exception as exc:
                write("WARN get_quest_difficulties failed on {!r}: {}: {}".format(
                    area_name(area, "?"), type(exc).__name__, exc))
        elif not state["scan_warned"]:
            state["scan_warned"] = True
            write("WARN scripts.functions.get_quest_difficulties not found; "
                  "scanning world.quests instead")
        quests = getattr(world, "quests", None)
        found = []
        if isinstance(quests, dict):
            wanted = ui.area_id_of(area)
            for quest in quests.values():
                owner = ui.quest_value(quest, QUEST_AREA_KEY, None)
                if owner is not None and str(owner) == wanted:
                    value = ui.quest_value(quest, QUEST_DIFFICULTY_KEY, None)
                    if _number(value):
                        found.append(value)
        return found

    def level_of(app, area):
        """その土地の適正（依頼の難易度の平均）。依頼が無ければ None（未訪問の街）。"""
        values = difficulties_of(app, area)
        if not values:
            return None
        return max(MIN_DIFFICULTY, int(round(sum(values) / float(len(values)))))

    def candidates_of(app, origin):
        """まだ繋がっていない街の一覧 `[(area, hops), ...]`。id の順。"""
        areas = ui.world_areas(app)
        origin_id = ui.area_id_of(origin)
        sizes = parse_sizes(TOWN_SIZES)
        graph = build_graph(areas)
        linked = graph.get(origin_id, set())
        found = []
        unsized = []
        for area_id in sorted(areas, key=ui.id_sort_key):
            area = areas[area_id]
            aid = str(area_id)
            if aid == origin_id or aid in linked:
                continue
            size = size_of(app, area, aid)
            if size is None:
                unsized.append(aid)
                continue
            if size not in sizes:
                continue
            hops = hops_between(graph, origin_id, aid)
            found.append((area, hops))
        if unsized and not state["unsized_noted"]:
            # 種類が読めない土地は候補にしない。黙って抜けず、一度だけ残す。
            state["unsized_noted"] = True
            write("WARN size: cannot read the size of area(s) {}; they cannot be "
                  "candidates (looked at Area.size, save_data_dict, world_dict)".format(unsized))
        return found

    def offer_of(app, origin, target, hops):
        """候補1つぶんの金額と難易度。孤立した街は `ISOLATED_HOPS` を使う。"""
        isolated = hops is None
        used = int(ISOLATED_HOPS) if isolated else int(hops)
        origin_level = level_of(app, origin)
        target_level = level_of(app, target)
        return {
            "hops": used,
            "isolated": isolated,
            "price": price_for(used),
            "difficulty": difficulty_for(origin_level, target_level, used),
            "origin_level": origin_level,
            "target_level": target_level,
        }

    # ------------------------------------------------------------ 接続の反映
    def apply_roads(world, key, why):
        """控えの道を実行中の `Area.connections` へ当てる。足した辺の数を返す。"""
        areas = ui.areas_of_world(world)
        if not areas:
            return 0
        added = 0
        missing = []
        for road in roads_of(key):
            a, b = str(road.get("from", "")), str(road.get("to", ""))
            side_a, side_b = areas.get(a), areas.get(b)
            if side_a is None or side_b is None:
                missing.append((a, b))
                continue
            added += int(add_connection(side_a, b)) + int(add_connection(side_b, a))
        if missing:
            write("WARN {}: {} road(s) point at areas this world does not have: {}"
                  .format(why, len(missing), missing))
        if added:
            write("{}: applied {} edge(s) from {} road(s) to world {!r}".format(
                why, added, len(roads_of(key)), key))
        return added

    def achievements_note(app, area, text):
        """`area_history.achievements` に1件足す。記録が無い（未訪問の）街には作らない。"""
        record = ui.area_record(getattr(app, "player", None), ui.area_id_of(area))
        if not isinstance(record, dict):
            return False
        items = record.get("achievements")
        if not isinstance(items, list):
            return False
        if text in items:
            return False
        items.append(text)
        return True

    def open_road(app, origin, target, via, extra, idle=False):
        """道を開く。両側の接続・控え・1行・功績。

        `idle=True` は日数送りの最中（移動・宿泊）に期日が来た委託。
        流れているテキストに割り込まず、手が空いてから1行出す（待ちきれなくても出す）。
        """
        key = world_key(app)
        origin_id, target_id = ui.area_id_of(origin), ui.area_id_of(target)
        origin_name = area_name(origin, "この土地")
        target_name = area_name(target, "その土地")
        added = int(add_connection(origin, target_id)) + int(add_connection(target, origin_id))
        bucket = bucket_of(key)
        already = any(str(r.get("from")) == origin_id and str(r.get("to")) == target_id
                      or str(r.get("from")) == target_id and str(r.get("to")) == origin_id
                      for r in roads_of(key))
        if not already:
            record = {"from": origin_id, "to": target_id,
                      "from_name": origin_name, "to_name": target_name,
                      "via": via, "day": ui.game_day(app),
                      "at": datetime.datetime.now().isoformat(timespec="seconds")}
            record.update(extra or {})
            bucket["roads"].append(record)
        worlds.save(key)
        write("opened: {!r}({}) <-> {!r}({}) via {} edges+{} recorded={} {}".format(
            origin_name, origin_id, target_name, target_id, via, added, not already,
            extra))
        if OPEN_TEXT:
            line = fmt(OPEN_TEXT, a=origin_name, b=target_name)
            if idle:
                screen.when_idle(app, lambda: screen.say(app, line),
                                 proceed_on_timeout=True, tag="open announce")
            else:
                screen.say(app, line)
        if via in ("pay", "commission") and ACHIEVEMENT_TEXT:
            text = fmt(ACHIEVEMENT_TEXT, a=origin_name, b=target_name)
            wrote = [ui.area_id_of(a) for a in (origin, target)
                     if achievements_note(app, a, text)]
            write("achievement: written to area_history of {}".format(wrote or "nobody"))
        return added

    # ------------------------------------------------------------ 画面
    def settle(app, then):
        screen.when_idle(app, then, settle=SETTLE, proceed_on_timeout=True, tag="settle")

    def show_candidates(app):
        origin = ui.current_area(app)
        if origin is None:
            write("search: no current area")
            return
        found = candidates_of(app, origin)
        if not found:
            screen.say(app, NO_CANDIDATE_TEXT)
            screen.apply_buttons(app, None, "no candidates")
            return
        entries = []
        for area, hops in found:
            offer = offer_of(app, origin, area, hops)
            label = fmt(CANDIDATE_LABEL, name=area_name(area, "?"),
                        price=ui.money(offer["price"]), difficulty=offer["difficulty"],
                        hops=offer["hops"])
            entry = screen.button(label, mark="pick",
                                  extra={TARGET_KEY: ui.area_id_of(area)})
            if entry is None:
                write("search: cannot build a button (PhaseSpec unavailable)")
                return
            entries.append(entry)
            write("candidate: {!r}({}) hops={}{} price={} difficulty={} "
                  "(levels origin={} target={})".format(
                      area_name(area, "?"), ui.area_id_of(area), offer["hops"],
                      " isolated" if offer["isolated"] else "", offer["price"],
                      offer["difficulty"], offer["origin_level"], offer["target_level"]))
        back = screen.button(BACK_LABEL)
        if back is not None:
            entries.append(back)
        screen.apply_buttons(app, entries, "candidates")

    def show_means(app, target_id):
        origin = ui.current_area(app)
        target = ui.world_areas(app).get(str(target_id))
        if origin is None or target is None:
            screen.say(app, NO_ROAD_TEXT)
            return
        graph = build_graph(ui.world_areas(app))
        offer = offer_of(app, origin, target, hops_between(graph, ui.area_id_of(origin),
                                                           str(target_id)))
        days = max(0, int(PAY_DAYS))
        pay = screen.button(fmt(PAY_LABEL if days > 0 else PAY_LABEL_NOW,
                                price=ui.money(offer["price"]), days=days),
                            mark="pay", extra={TARGET_KEY: str(target_id)})
        dungeon = screen.button(fmt(DUNGEON_LABEL, difficulty=offer["difficulty"]),
                                mark="dungeon", extra={TARGET_KEY: str(target_id)})
        back = screen.button(BACK_LABEL)
        if pay is None or dungeon is None or back is None:
            write("means: cannot build the buttons (PhaseSpec unavailable)")
            return
        write("means: {!r} price={} difficulty={} hops={}".format(
            area_name(target, "?"), offer["price"], offer["difficulty"], offer["hops"]))
        screen.apply_buttons(app, [pay, dungeon, back], "means")

    def reopen_move_list(app):
        """素の「他の土地へ行く」を開き直す（開いた道が並んでいるのを見せる）。"""
        display_cls = ui.cls_of("DisplayAreaMoveChoice")
        if display_cls is None:
            screen.apply_buttons(app, None, "paid")
            return
        try:
            manager = display_cls(app)
        except Exception:
            ctx.log_exc("road opening: cannot build DisplayAreaMoveChoice")
            return
        screen.start_phase(app, manager, "他の土地へ行く")

    # ------------------------------------------------------------ お金で開く
    def pay_road(app, target_id):
        origin = ui.current_area(app)
        target = ui.world_areas(app).get(str(target_id))
        if origin is None or target is None:
            screen.say(app, NO_ROAD_TEXT)
            return
        graph = build_graph(ui.world_areas(app))
        offer = offer_of(app, origin, target,
                         hops_between(graph, ui.area_id_of(origin), str(target_id)))
        price = offer["price"]
        waiting = commission_of(world_key(app), ui.area_id_of(origin), target_id)
        if waiting is not None:
            today = ui.game_day(app)
            left = max(0, int(waiting.get("due_day", 0)) - int(today)) if today is not None else "?"
            write("pay: {!r} is already commissioned (due day {}); not charging".format(
                area_name(target, "?"), waiting.get("due_day")))
            screen.say(app, ALREADY_COMMISSIONED_TEXT.format(
                target=area_name(target, "その土地"), days=left))
            settle(app, lambda: reopen_move_list(app))
            return
        if str(target_id) in connections_of(origin):
            # 二重押しの2発目（旗をすり抜けた場合）や、開いた直後の古い画面からの押下。
            # **支払わない。** 開いた道は一度きり。
            write("pay: {!r} is already linked to {!r}; not charging".format(
                area_name(target, "?"), area_name(origin, "?")))
            screen.say(app, ALREADY_TEXT.format(target=area_name(target, "その土地")))
            settle(app, lambda: reopen_move_list(app))
            return
        gold = ui.gold_of(app)
        if gold is None:
            write("pay: cannot read the player's gold; refusing")
            screen.say(app, NO_ROAD_TEXT)
            return
        if gold < price:
            write("pay: refused; price {} > gold {}".format(price, gold))
            screen.say(app, fmt(NO_GOLD_TEXT, price=ui.money(price), gold=ui.money(gold)))
            settle(app, lambda: show_means(app, target_id))
            return
        after = ui.add_gold(app, -price,
                            on_error=lambda msg: write("WARN pay: " + msg))
        if after is None:
            screen.say(app, NO_ROAD_TEXT)
            return
        write("pay: {} -> {} (price {})".format(gold, after, price))
        days = max(0, int(PAY_DAYS))
        today = ui.game_day(app) if days > 0 else None
        if days > 0 and today is None:
            write("WARN pay: cannot read the game day; opening the road now instead of "
                  "in {} day(s)".format(days))
        if days <= 0 or today is None:
            screen.say(app, fmt(PAID_TEXT, price=ui.money(price),
                                target=area_name(target, "その土地")))
            open_road(app, origin, target, "pay", {"price": price, "hops": offer["hops"]})
        else:
            key = world_key(app)
            bucket = bucket_of(key)
            bucket.setdefault("commissions", []).append({
                "from": ui.area_id_of(origin), "to": str(target_id),
                "from_name": area_name(origin, "この土地"),
                "to_name": area_name(target, "その土地"),
                "price": price, "hops": offer["hops"],
                "paid_day": today, "due_day": today + days,
                "at": datetime.datetime.now().isoformat(timespec="seconds"),
            })
            worlds.save(key)
            write("commissioned: {!r} <-> {!r} due day {} (today {} + {})".format(
                area_name(origin, "?"), area_name(target, "?"), today + days, today, days))
            if COMMISSION_TEXT:
                screen.say(app, fmt(COMMISSION_TEXT, price=ui.money(price),
                                    target=area_name(target, "その土地"), days=days))
        refresh_status(app)
        settle(app, lambda: reopen_move_list(app))

    def commission_of(key, origin_id, target_id):
        """その2つの街の間で期日待ちの委託。無ければ None。"""
        ends = {str(origin_id), str(target_id)}
        for item in bucket_of(key).get("commissions") or []:
            if isinstance(item, dict) and {str(item.get("from")), str(item.get("to"))} == ends:
                return item
        return None

    def check_commissions(app, why):
        """期日が来た委託を開く。開いた数を返す。日数が進んだとき・画面が組み直されたときに呼ぶ。"""
        if app is None:
            return 0
        key = world_key(app)
        bucket = bucket_of(key)
        items = [c for c in bucket.get("commissions") or [] if isinstance(c, dict)]
        if not items:
            return 0
        today = ui.game_day(app)
        if today is None:
            return 0
        areas = ui.world_areas(app)
        keep = []
        opened = 0
        for item in items:
            try:
                due = int(item.get("due_day"))
            except (TypeError, ValueError):
                write("WARN commission without a due day dropped: {!r}".format(item))
                continue
            if due > today:
                keep.append(item)
                continue
            origin = areas.get(str(item.get("from")))
            target = areas.get(str(item.get("to")))
            if origin is None or target is None:
                write("WARN {}: commission {!r}->{!r} points at areas this world does not "
                      "have; dropped".format(why, item.get("from"), item.get("to")))
                continue
            write("{}: commission due (day {} >= {}); opening {!r} <-> {!r}".format(
                why, today, due, item.get("from_name"), item.get("to_name")))
            open_road(app, origin, target, "commission",
                      {"price": item.get("price"), "hops": item.get("hops"),
                       "paid_day": item.get("paid_day")}, idle=True)
            opened += 1
        if len(keep) != len(items):
            bucket["commissions"] = keep
            worlds.save(key)
        return opened

    def refresh_status(app):
        """所持金の表示を今の値に合わせる（`309_` と同じ。無ければ次にゲームが塗る）。"""
        update = getattr(app, "update_ui", None)
        if not callable(update):
            return

        def run():
            try:
                update()
            except Exception:
                ctx.log_exc("road opening: update_ui failed")

        screen.schedule(run, 0)

    # ------------------------------------------------------------ ダンジョン踏破
    def stamina_refusal(app):
        percent = max(0, min(100, int(STAMINA_MIN_PERCENT)))
        if percent <= 0:
            return None
        player = getattr(app, "player", None)
        value = getattr(player, "physical_integrity", None)
        limit = getattr(player, "max_physical_integrity", None)
        if not _number(limit) or limit <= 0:
            limit = getattr(player, "original_max_physical_integrity", None)
        if not _number(value) or not _number(limit) or limit <= 0:
            write("WARN stamina: cannot read physical_integrity; letting the road through")
            return None
        if value * 100 >= limit * percent:
            return None
        return REFUSE_DETAIL.format(value=int(value), limit=int(limit))

    def start_dungeon(app, target_id):
        if state["generating"]:
            screen.say(app, "……いま道の話を聞いているところだ。")
            return
        origin = ui.current_area(app)
        target = ui.world_areas(app).get(str(target_id))
        if origin is None or target is None:
            screen.say(app, NO_ROAD_TEXT)
            return
        refusal = stamina_refusal(app)
        if refusal is not None:
            write("refused: not enough stamina {}".format(refusal))
            screen.say(app, REFUSE_TEXT)
            screen.say(app, refusal)
            settle(app, lambda: show_means(app, target_id))
            return
        graph = build_graph(ui.world_areas(app))
        offer = offer_of(app, origin, target,
                         hops_between(graph, ui.area_id_of(origin), str(target_id)))
        origin_name = area_name(origin, "この土地")
        target_name = area_name(target, "その土地")
        difficulty = offer["difficulty"]
        write("=" * 78)
        write("dungeon: {!r}({}) -> {!r}({}) hops={} difficulty={} (mode={} per_hop={} "
              "offset={} levels origin={} target={})".format(
                  origin_name, ui.area_id_of(origin), target_name, str(target_id),
                  offer["hops"], difficulty, DIFFICULTY_MODE, DIFFICULTY_PER_HOP,
                  DIFFICULTY_OFFSET, offer["origin_level"], offer["target_level"]))

        quest_id, quest = generate(app, origin_name, target_name, difficulty)
        if quest_id is None:
            return
        if pending_of(app) is not None:
            drop_pending(app, "replaced by a new road")
        set_pending(app, {
            "stage": "offered",
            "quest_id": str(quest_id),
            "origin_id": ui.area_id_of(origin),
            "origin_name": origin_name,
            "target_id": str(target_id),
            "target_name": target_name,
            "difficulty": difficulty,
            "hops": offer["hops"],
            "at": time.time(),
            "days_spent": 0,
            "moving_at": 0.0,
        })
        title = ui.quest_value(quest, "quest_title", "") or ""
        screen.busy_off(app, restore=False)
        settle(app, lambda: open_acceptance(app, quest_id, title, target_name, difficulty))

    def generate(app, origin_name, target_name, difficulty):
        """ゲーム自身の生成経路を、道の性質を添えて呼ぶ。`(id, quest)`。ここで最後までやりきる。"""
        display_cls = ui.cls_of("DisplayQuestChoice")
        if display_cls is None:
            write("dungeon: DisplayQuestChoice not found")
            screen.say(app, NO_ROAD_TEXT)
            return None, None
        state["generating"] = True
        state["inject"] = {"origin": origin_name, "target": target_name,
                           "difficulty": difficulty}
        state["inject_at"] = time.monotonic()
        screen.busy_on(app)
        screen.say(app, LOOKING_TEXT.format(target=target_name))
        started = time.monotonic()
        before = set(ui.quest_ids(app))
        try:
            display_cls(app).generate_random_quest()
        except Exception:
            ctx.log_exc("road opening: generate_random_quest failed")
            state["generating"] = False
            state["inject"] = None
            screen.busy_off(app)
            settle(app, lambda: screen.say(app, NO_QUEST_TEXT))
            return None, None
        added = sorted(set(ui.quest_ids(app)) - before, key=ui.id_sort_key)
        state["generating"] = False
        state["inject"] = None
        write("dungeon: took {:.1f}s; new quest ids={}".format(
            time.monotonic() - started, added))
        if not added:
            screen.busy_off(app)
            settle(app, lambda: screen.say(app, NO_QUEST_TEXT))
            return None, None
        quest_id = added[-1]
        write_difficulty(app, quest_id, difficulty, "generated")
        note_arrival(app, quest_id, origin_name, target_name)
        return quest_id, ui.quest_of(app, quest_id)

    def open_acceptance(app, quest_id, title, target_name, difficulty):
        screen.say(app, FOUND_TEXT.format(title=title or "名も無い道", target=target_name))
        screen.say(app, DANGER_TEXT.format(difficulty=difficulty, target=target_name))
        choice_cls = ui.cls_of("QuestChoiceManager")
        if choice_cls is not None:
            try:
                manager = choice_cls(app, QUEST_TYPE, str(quest_id))
            except Exception:
                ctx.log_exc("road opening: QuestChoiceManager({!r}, {!r}) failed".format(
                    QUEST_TYPE, quest_id))
                manager = None
            if manager is not None:
                write("acceptance: process_choice(QuestChoiceManager, {!r})".format(quest_id))
                screen.start_phase(app, manager, title or SEARCH_LABEL)
                return
        display_cls = ui.cls_of("DisplayQuestChoice")
        if display_cls is None:
            write("acceptance: no way to open the quest; it is still listed")
            return
        write("acceptance: falling back to the quest board")
        screen.start_phase(app, display_cls(app), "クエスト掲示板")

    def observe_quest(app):
        """ゲームが道中のクエストを進めているなら控えを本決まりにする（`307_` と同じ）。"""
        record = pending_of(app, "offered")
        if record is None:
            return False
        quest = getattr(app, "current_quest_data", None)
        current = quest.get("id") if isinstance(quest, dict) else getattr(quest, "id", None)
        if current is None or str(current) != record.get("quest_id"):
            return False
        advance(app, "armed", at=time.time())
        write("armed: the game is running quest {!r}; the road to {!r} waits at the end"
              .format(record.get("quest_id"), record.get("target_name")))
        return True

    def current_quest_id(app):
        quest = getattr(app, "current_quest_data", None) if app is not None else None
        if quest is None:
            return None
        value = quest.get("id") if isinstance(quest, dict) else getattr(quest, "id", None)
        return str(value) if value is not None else None

    def arrived_check(app):
        record = pending_of(app, "moving")
        if record is None:
            return False
        if ui.area_id_of(ui.current_area(app)) != record.get("target_id"):
            return False
        write("arrived: {!r} reached; the move took {} day(s) (set to {})".format(
            record.get("target_name"), record.get("days_spent"), ARRIVAL_DAYS))
        if not record.get("days_spent"):
            write("WARN arrived: elapse_days was never seen; "
                  "the {}-day setting did not apply to this build".format(ARRIVAL_DAYS))
        drop_pending(app, "arrived", clear_note=False)
        return True

    def in_settlement(buttons):
        if not isinstance(buttons, (list, tuple)):
            return False
        return any(ui.spec_cls_name(entry) in SETTLEMENT_MARKS for entry in buttons)

    def depart(app):
        """開いた道を通って対象の街へ。実測の `mode` で組み、組めなければ確認画面へ落とす。"""
        record = pending_of(app, "ready")
        if record is None:
            return
        target_id = str(record.get("target_id"))
        target_name = record.get("target_name") or "目的地"
        cls = ui.cls_of("AreaMoveManager")
        manager = None
        if cls is not None:
            try:
                manager = cls(app, target_id, WALK_MODE)
            except Exception:
                ctx.log_exc("road opening: AreaMoveManager({!r}, {!r}) failed".format(
                    target_id, WALK_MODE))
        if manager is None:
            confirm_cls = ui.cls_of(TOWN_CLS)
            if confirm_cls is None:
                write("arrive: neither AreaMoveManager nor {} is available; "
                      "the road is open, travel by the normal list".format(TOWN_CLS))
                drop_pending(app, "no way to move", clear_note=False)
                return
            write("arrive: falling back to the confirmation screen for {!r}".format(
                target_name))
            drop_pending(app, "handed over to the confirmation screen", clear_note=False)
            try:
                settle(app, lambda: screen.start_phase(app, confirm_cls(app, target_id),
                                                       target_name))
            except Exception:
                ctx.log_exc("road opening: cannot open the confirmation screen")
            return
        advance(app, "moving", moving_at=time.time())
        screen.say(app, ARRIVE_TEXT.format(target=target_name))
        write("arrive: process_choice(AreaMoveManager, {!r}, {!r})".format(
            target_id, WALK_MODE))
        state["departing"] = True
        try:
            screen.start_phase(app, manager, target_name)
        finally:
            state["departing"] = False
        arrived_check(app)

    # ------------------------------------------------------------ 自前のフェーズ
    class RoadPhase(object):
        """自前のフェーズ。**`PhaseSpec` には決して載せない**（セーブに焼かれる）。"""

        def __init__(self, app, action, target_id):
            self.app = app
            self.action = action
            self.target_id = target_id

        def execute(self, choice_text):
            state["acting"] = True
            try:
                run_action(self.app, self.action, self.target_id)
            except Exception:
                ctx.log_exc("road opening: phase {!r} failed".format(self.action))
            finally:
                state["acting"] = False

    def run_action(app, action, target_id):
        if action == "search":
            show_candidates(app)
        elif action == "pick":
            show_means(app, target_id)
        elif action == "pay":
            pay_road(app, target_id)
        elif action == "dungeon":
            start_dungeon(app, target_id)
        else:
            write("WARN unknown action {!r}".format(action))

    # ================================================================ フック
    @ctx.wrap("__main__:DisplayAreaMoveChoice.update_button_display", required=False)
    def move_choice_buttons(orig, self, *args, **kwargs):
        """行き先が並ぶ前に開いた道を当て直し、並び終えた後に「新たな道を探す」を足す。

        ゲームが `connections` を別の場所（骨格の辞書）から読んでいて開いた道が
        並ばなかったときは、同じ形（`AreaMoveCofirmation` + `[target_area_id]`）の
        ボタンをこちらで足し、WARN を残す（HANDOVER §6 の1）。
        """
        app = getattr(self, "app", None) or ui.find_app()
        try:
            if app is not None:
                apply_roads(getattr(app, "world", None), world_key(app), "list")
        except Exception:
            ctx.log_exc("road opening: cannot re-apply the roads before the list")
        result = orig(self, *args, **kwargs)
        try:
            buttons = getattr(app, "buttons", None) if app is not None else None
            if not isinstance(buttons, list):
                return result
            origin = ui.current_area(app)
            if origin is None:
                return result
            origin_id = ui.area_id_of(origin)
            areas = ui.world_areas(app)
            listed = set()
            for entry in buttons:
                if ui.spec_cls_name(entry) == TOWN_CLS:
                    argv = ui.spec_args(entry)
                    if argv:
                        listed.add(str(argv[0]))
            at = len(buttons)
            for index, item in enumerate(buttons):
                if ui.spec_cls_name(item) == ui.SAFE_CLS:
                    at = index
                    break
            for road in roads_of(world_key(app)):
                ends = {str(road.get("from")), str(road.get("to"))}
                if origin_id not in ends:
                    continue
                other = (ends - {origin_id}).pop() if len(ends) == 2 else None
                if other is None or other in listed or other not in areas:
                    continue
                entry = screen.button(area_name(areas[other], other),
                                      cls_name=TOWN_CLS, args=[other])
                if entry is None:
                    break
                buttons.insert(at, entry)
                at += 1
                listed.add(other)
                write("WARN list: the game did not list the opened road to {!r}; "
                      "added the button myself (the game reads connections from "
                      "somewhere other than Area.connections)".format(other))
            screen.prune_stale(buttons, OUR_LABEL_PREFIXES)
            if any(screen.mark_of(entry) for entry in buttons):
                return result
            if not candidates_of(app, origin):
                write("list: no unlinked town from {!r}; not adding {!r}".format(
                    area_name(origin, origin_id), SEARCH_LABEL))
                screen.apply_buttons(app, None, "list")
                return result
            entry = screen.button(SEARCH_LABEL, mark="search")
            if entry is None:
                return result
            buttons.insert(at, entry)
            screen.apply_buttons(app, None, "list")
        except Exception:
            ctx.log_exc("road opening: cannot add the search button")
        return result

    @ctx.wrap("__main__:InstantaleApp.on_button_press", required=False)
    def on_button_press(orig, self, button_index, *args, **kwargs):
        """自前のボタンだけ横取りする。印が無ければ必ず素通し。"""
        entry = ui.pressed_entry(self, button_index)
        action = screen.mark_of(entry)
        if action is None:
            return orig(self, button_index, *args, **kwargs)
        if state["acting"] or state["generating"]:
            # 前の押下がまだ動いている。連打の2発目は捨てる（支払いの二重を防ぐ）。
            write("ignored {!r}: the previous press is still running".format(
                entry.get("text") if isinstance(entry, dict) else None))
            return None
        text = (entry.get("text") if isinstance(entry, dict) else None) or SEARCH_LABEL
        target_id = entry.get(TARGET_KEY) if isinstance(entry, dict) else None
        write("pressed {!r} ({} target={})".format(text, action, target_id))
        screen.start_phase(self, RoadPhase(self, action, target_id), text,
                           fallback=lambda: run_action(self, action, target_id))
        return None

    @ctx.wrap("__main__:World.__init__", required=False, safe=True)
    def world_loaded(orig, self, save_data_dict, app, *args, **kwargs):
        """セーブを読み込んだ直後、開いた道をこの世界へ当て直す（`318_` / `321_` と同型）。"""
        result = orig(self, save_data_dict, app, *args, **kwargs)
        try:
            key = world_key_of_dict(save_data_dict, None) or world_key(app)
            if key and key != UNKNOWN_WORLD:
                worlds.forget(key)
                apply_roads(self, key, "load")
        except Exception:
            ctx.log_exc("road opening: cannot re-apply the roads on load")
        return result

    @ctx.wrap("__main__:InstantaleApp.refresh_choice_buttons", required=False, safe=True)
    def refresh_choice_buttons(orig, self, reset_page=False, *args, **kwargs):
        """集落の選択肢に戻ったら、待っている移動を起こす（保険の経路。`307_` と同じ）。"""
        try:
            observe_quest(self)
            arrived_check(self)
            check_commissions(self, "refresh")
            record = pending_of(self, "ready")
            if record is not None and in_settlement(getattr(self, "buttons", None)):
                advance(self, "arriving")
                write("arrive: back in a settlement; leaving for {!r}".format(
                    record.get("target_name")))

                def go():
                    current = pending_of(self)
                    if current is not None and current.get("stage") == "arriving":
                        advance(self, "ready")
                    depart(self)

                screen.when_idle(self, go, settle=SETTLE, proceed_on_timeout=True,
                                 tag="arrive")
        except Exception:
            ctx.log_exc("road opening: cannot start the pending move")
        return orig(self, reset_page, *args, **kwargs)

    @ctx.wrap("__main__:QuestStartManager.__init__", required=False, safe=True)
    def quest_start(orig, self, app, quest_type, quest_id, *args, **kwargs):
        """道中のクエストが始まったら控えを本決まりにし、難易度と一文を書き直す。"""
        try:
            record = pending_of(app, "offered", "armed")
            if record is not None:
                if str(quest_id) == record.get("quest_id"):
                    advance(app, "armed", at=time.time())
                    write("armed: quest {!r} started; the road to {!r} waits at the end"
                          .format(quest_id, record.get("target_name")))
                    if isinstance(record.get("difficulty"), int):
                        write_difficulty(app, quest_id, record["difficulty"], "armed")
                    note_arrival(app, quest_id, record.get("origin_name"),
                                 record.get("target_name"))
                else:
                    drop_pending(app, "another quest started ({})".format(quest_id))
        except Exception:
            ctx.log_exc("road opening: cannot arm the road")
        return orig(self, app, quest_type, quest_id, *args, **kwargs)

    @ctx.wrap("__main__:QuestEndManager.execute", required=False, safe=True)
    def quest_end(orig, self, *args, **kwargs):
        """踏破。道を開き、帰還処理が済んだその場で移動する。"""
        app = getattr(self, "app", None) or ui.find_app()
        ended = None
        try:
            ended = current_quest_id(app)
        except Exception:
            ctx.log_exc("road opening: cannot read the current quest")
        result = orig(self, *args, **kwargs)
        try:
            record = pending_of(app, "offered", "armed")
            if record is not None and (ended == record.get("quest_id")
                                       or (ended is None
                                           and record.get("stage") == "armed")):
                areas = ui.world_areas(app)
                origin = areas.get(str(record.get("origin_id")))
                target = areas.get(str(record.get("target_id")))
                if origin is None or target is None:
                    write("WARN cleared: areas {!r}/{!r} not found; cannot open".format(
                        record.get("origin_id"), record.get("target_id")))
                    drop_pending(app, "areas missing")
                else:
                    open_road(app, origin, target, "dungeon",
                              {"difficulty": record.get("difficulty"),
                               "hops": record.get("hops"),
                               "quest_id": record.get("quest_id")})
                    advance(app, "ready", at=time.time())
                    write("cleared: quest {!r} ended; leaving for {!r}".format(
                        ended or record.get("quest_id"), record.get("target_name")))
                    settle(app, lambda: depart(app))
            elif record is not None:
                write("quest {!r} ended but the road is for {!r}; not opening".format(
                    ended, record.get("quest_id")))
        except Exception:
            ctx.log_exc("road opening: cannot open the road on clear")
        return result

    @ctx.wrap("__main__:QuestRetireManager.execute", required=False, safe=True)
    def quest_retire(orig, self, *args, **kwargs):
        """放棄。道は開かない。出発地に留まる。"""
        app = getattr(self, "app", None) or ui.find_app()
        ended = None
        try:
            ended = current_quest_id(app)
        except Exception:
            ctx.log_exc("road opening: cannot read the current quest")
        result = orig(self, *args, **kwargs)
        try:
            record = pending_of(app, "offered", "armed", "ready")
            if record is not None and ended is not None \
                    and ended != record.get("quest_id"):
                write("quest {!r} was abandoned but the road is for {!r}; leaving it"
                      .format(ended, record.get("quest_id")))
            elif record is not None and (ended is not None
                                         or record.get("stage") != "offered"):
                origin = record.get("origin_name") or "元の土地"
                target = record.get("target_name") or "その街"
                drop_pending(app, "the quest was abandoned")
                settle(app, lambda: screen.say(
                    app, RETIRE_TEXT.format(origin=origin, target=target)))
        except Exception:
            ctx.log_exc("road opening: cannot cancel the road")
        return result

    @ctx.wrap("__main__:AreaMoveManager.__init__", required=False, safe=True)
    def area_move(orig, self, app, target_area_id, mode, *args, **kwargs):
        """自分が起こしたのでない移動が始まったら、待っている道中は捨てる。"""
        try:
            if not state["departing"] and pending_of(app, "offered", "armed") is not None:
                drop_pending(app, "the player travelled by other means (mode={!r})"
                             .format(mode))
        except Exception:
            ctx.log_exc("road opening: cannot clear the pending road")
        return orig(self, app, target_area_id, mode, *args, **kwargs)

    @ctx.wrap("__main__:InstantaleApp.add_text", required=False)
    def add_text(orig, self, context, *args, **kwargs):
        """到着の移動中だけ、ゲームの「徒歩で目指す。長旅だ...」を伏せる（`307_` と同じ）。"""
        try:
            record = pending_of(self, "moving") if HIDE_TRAVEL_TEXT else None
            if record is not None and isinstance(context, str) \
                    and any(word in context for word in MUTED_MOVE_TEXTS):
                write("muted while arriving: {!r}".format(context))
                return None
        except Exception:
            ctx.log_exc("road opening: cannot filter the travel text")
        return orig(self, context, *args, **kwargs)

    @ctx.wrap("__main__:InstantaleApp.elapse_days", required=False)
    def elapse_days(orig, self, days, *args, **kwargs):
        """到着の移動にかかる日数を `ARRIVAL_DAYS` にする。段階 `moving` の最初の1回だけ。

        日数が進んだ後は、期日が来た委託を開く（`check_commissions`）。
        """
        granted = days
        try:
            observe_quest(self)
            record = pending_of(self, "moving")
            if record is not None and _number(days) and days > 0:
                if int(record.get("days_spent") or 0) > 0:
                    write("days: {} left alone (the road to {!r} already took {} day(s))"
                          .format(days, record.get("target_name"), record.get("days_spent")))
                else:
                    granted = max(0, int(ARRIVAL_DAYS))
                    advance(self, "moving", days_spent=granted)
                    write("days: {} -> {} (the road to {!r} takes {} day(s))".format(
                        days, granted, record.get("target_name"), granted))
        except Exception:
            ctx.log_exc("road opening: cannot set the days")
            granted = days
        result = orig(self, granted, *args, **kwargs)
        try:
            check_commissions(self, "days")
        except Exception:
            ctx.log_exc("road opening: cannot check the commissions")
        return result

    @ctx.wrap("scripts.llm.llm_manager_world_generate:random_quest_generator",
              required=False)
    def random_quest_generator(orig, world_overview, settlement_name,
                               settlement_overview, settlement_structure_description,
                               area_description, quest_difficulty, *args, **kwargs):
        """`area_description` に道の性質を、`quest_difficulty` に選んだ値を渡す。1回で使い切る。"""
        mark = state["inject"]
        state["inject"] = None
        if mark is None or time.monotonic() - state["inject_at"] > INJECT_TTL:
            if mark is not None:
                write("inject: stale marker, left untouched")
            return orig(world_overview, settlement_name, settlement_overview,
                        settlement_structure_description, area_description,
                        quest_difficulty, *args, **kwargs)
        merged = (area_description or "") + road_brief(
            mark["origin"], mark["target"], mark["difficulty"])
        write("inject: area_description {} -> {} chars; difficulty {!r} -> {}".format(
            len(area_description or ""), len(merged), quest_difficulty,
            mark["difficulty"]))
        result = orig(world_overview, settlement_name, settlement_overview,
                      settlement_structure_description, merged,
                      mark["difficulty"], *args, **kwargs)
        if isinstance(result, dict):
            write("inject: generated {!r}".format(result.get("quest_title")))
        return result

    # ------------------------------------------------------------ 初回の当て直し
    def initial_apply():
        """世界が既に読み込まれた後に注入されたとき（`World.__init__` を通らない）。"""
        app = ui.find_app()
        world = getattr(app, "world", None) if app is not None else None
        if world is None:
            return
        try:
            key = world_key(app)
            if key and key != UNKNOWN_WORLD:
                apply_roads(world, key, "ready")
        except Exception:
            ctx.log_exc("road opening: cannot re-apply the roads at start")

    ctx.on_ready(initial_apply)

    # ------------------------------------------------------------ 自己検証
    # 実経路は一覧を開くまで通らない。距離補正だけは作ったデータで先に確かめておく。
    class _A(object):
        def __init__(self, aid, size, links):
            self.id, self.size, self.connections = aid, size, list(links)
    sample = {"0": _A("0", "town", ["1", "4"]), "1": _A("1", "village", ["0", "2"]),
              "2": _A("2", "town", ["1"]), "4": _A("4", "village", ["0"]),
              "9": _A("9", "dungeon", []), "21": _A("21", "village", [])}
    graph = build_graph(sample)
    probe_a = _A("x", "town", ["1"])
    grew = add_connection(probe_a, "2") and probe_a.connections == ["1", "2"] \
        and not add_connection(probe_a, "2")
    class _App(object):
        world_dict = {"areas": {"5": {"size": "town"}}}
    sized = is_town(_App(), _A("5", None, []), {"town"}) \
        and not is_town(_App(), _A("6", None, []), {"town"}) \
        and is_town(_App(), _A("9", "city", []), {"city"})
    if sized and hops_between(graph, "0", "2") == 1 and hops_between(graph, "2", "4") == 2 \
            and hops_between(graph, "0", "1") == 0 and hops_between(graph, "0", "21") is None \
            and price_for(2) == int(PRICE_BASE) + 2 * int(PRICE_PER_HOP) \
            and difficulty_for(None, None, 0) == MIN_DIFFICULTY + max(0, int(DIFFICULTY_OFFSET)) \
            and grew and fmt("{a}と{typo}", a="砦") == "砦と{typo}":
        ctx.log("verified: BFS hops, price, size lookup and connection replacement")
    else:
        ctx.log("VERIFY FAILED: sized={} hops={} {} {} {} price={} grew={}".format(
            sized, hops_between(graph, "0", "2"), hops_between(graph, "2", "4"),
            hops_between(graph, "0", "1"), hops_between(graph, "0", "21"),
            price_for(2), grew), level="ERROR")

    ctx.log("road opening: price={}+{}/hop difficulty={} +{}/hop offset={} isolated={} "
            "arrival_days={} stamina>={}% log={}".format(
                PRICE_BASE, PRICE_PER_HOP, DIFFICULTY_MODE, DIFFICULTY_PER_HOP,
                DIFFICULTY_OFFSET, ISOLATED_HOPS, ARRIVAL_DAYS, STAMINA_MIN_PERCENT,
                log_path))
