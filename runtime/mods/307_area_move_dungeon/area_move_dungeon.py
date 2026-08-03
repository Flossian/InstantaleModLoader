# -*- coding: utf-8 -*-
"""機能追加: エリア移動の第3の手段「危険な道を行く」。

土地から土地へ移る確認画面（`AreaMoveCofirmation`。徒歩と馬車が並ぶところ）に
**「危険な道を行く」** を1つ足す。押すと、その2つの土地を繋ぐ道中を舞台にした
クエストが1件その場で作られ、受注画面に入る。**踏破すれば目的地に着く**。

    [確認画面]  徒歩(3ヵ月) / 馬車(1000G) / 危険な道を行く
                                              ↓
                道中を舞台にしたクエストを生成 → ゲーム本来の受注画面
                                              ↓
                踏破 → 目的地へ移動        放棄 → 移動しない（出発地に留まる）

## ファイルの分かれ方

| ファイル | 中身 |
|---|---|
| `area_move_dungeon.py`（ここ） | 設定・文言・この MOD の方針・フックの設置 |
| `journey.py` | 道中の控え（段階・保存・日数の予算）。ゲームには触らない |
| `world.py` | ゲームのデータの読み書き。この MOD の方針は持たない |

設定（`mod.json` から変えられる値）は**必ずこのファイルの定数**にすること。
ローダは入口モジュールのグローバルへ書き込むので、他のファイルへ移すと
GUI から変えても効かなくなる（TECH.md §3.8）。

## 難易度は移動元と移動先の間から選ぶ

    scripts.functions:get_area_average_difficulty(area, world, ...)

ゲーム自身が持っている「その土地の依頼はどのくらいの難易度か」を移動元と移動先の
両方に聞き、**その間から1つ選ぶ**（`DIFFICULTY_MODE`）。式を発明しないで済むうえ、
世界の作り方が変わっても追随する。引けなかったときの落とし方は `world.area_level`。

## 値の語彙を推測しない（`mode` を発明しない）

    __main__:AreaMoveManager.__init__(self, app, target_area_id, mode)

`mode` の実値は `'on_foot'` / `'coach'`（実測）だが、組み立てには使わない。
**確認画面に既に並んでいるゲーム自身のボタンから `args` をそのまま写す**。
値の意味を知らなくても正しく起こせる（TECH.md §6.2）。実測値は「どれが徒歩か」の
照合にだけ使い、当たらなければ文字列 → 並び順の順に下がる（`world.pick_option`）。

## 自前のクラス名を `PhaseSpec` に書かない

ボタンはセーブに焼かれうる（`PhaseSpec.to_dict()`）。無害な既存クラスを持たせ、
押下は `on_button_press` を包んでボタン辞書の印で横取りする。印のキーは他の MOD と
別にすること（`301_`=mod_action / `302_`=mod_party_action / `305_`=mod_mini_action /
ここ=mod_road_action）。

## 移動を起こすのは帰還処理が済んだ直後

**戦利品（`LootPhaseManager`）はクエスト完了より前**にあるので（GAME.md §2.9）、
`QuestEndManager.execute` が返った時点で取りこぼすものは無い。手が空くのを待って
（報酬のテキストを流している最中に割り込まない）すぐ移動する。

**「選択肢が集落のものに戻ってから」では遅い。** 帰還先はエリアの**入口**で、
そこの選択肢は隣の施設への `MovePhaseManager` だけなので、**一度元の街に戻され、
出口まで歩いて初めて移動する**ことになる。集落の画面を見る経路は保険として残して
ある（完了の瞬間を捉えられなかったときの受け皿。目印に `MovePhaseManager` も
入れたので入口でも拾える）。

## 状態はゲーム自身に聞く（一瞬の合図に頼らない）

道と受注を `QuestStartManager.__init__` だけで結び付けると、**その一瞬に
注入し直されたとき永久に結び付かない**。判定の根拠は `app.current_quest_data` の
id。いつ見ても答えが返る。`QuestStartManager` の経路も残してあるが、もう必須では
ない。控えが注入をまたぐ仕組みは `journey.py`。

## 危険だが**早い**（日数の上限を持つ）

    __main__:InstantaleApp.elapse_days(self, days)

`__main__` にある唯一の日数送りの入口。危険な道を行っている間だけこれを包み、
**道中と最後の移動を合わせて `TRAVEL_DAYS` 日（既定14＝馬車と同じ）を超えないよう
渡す数を減らす**。`orig` は必ず呼ぶので、暦の進め方も日次処理もゲームのまま。

これが無いと、道中のクエストで日数が進んだうえに最後の移動で徒歩ぶん（実測3ヵ月）が
乗り、**徒歩より遅くて危険なだけの道**になる。切り詰めるのは道を行っている間だけで、
訓練・休養・他の依頼の日数送りには一切触らない。

## 体力（スタミナ）が3分の1を切っていたら断る

`Character.physical_integrity` / `max_physical_integrity`（実測。`world.stamina_of`
と GAME.md §2.19）。`STAMINA_MIN_PERCENT` を下回っていたら**押された時点で断る**。
クエストの生成にも世界のデータにも一切触らない。読めなかったときは通す
（値が読めないことを理由に遊びを止めない。WARN は残す）。
"""

import datetime
import random
import time

from instantale_modloader import ui

from . import world
from .journey import Journey


LOG_BASENAME = "road_travel.log"

# 進行中の道中の控え。**セーブには書かない。**
STATE_BASENAME = "road_travel.json"

# 確認画面に足すボタンの文字列。
ROAD_LABEL = "危険な道を行く"

# セーブから復元された残骸を見分けるための、こちらのラベル一覧
# （`ui.Screen.prune_stale`）。`PhaseSpec.to_dict()` は text と spec しか書か
# ないので**印は落ちる**。落ちたものは下の印による重複判定をすり抜け、同じ
# ボタンが2つ並んで復元された方は押しても無反応になる（`301_` で実際に起きた）。
#
# **ここは実機で観測した症状ではなく、保険**（2026-08-03）。`301_` / `309_` が
# 踏んだのは `refresh_choice_buttons`（ゲームが組んだ一覧を塗り直すだけ）で、
# こちらは徒歩・馬車が並び終えた後 ― 確認画面がそのつど一覧を組み直すビルド
# なら残骸はゲーム自身が消している。組み直さないビルドがあっても壊れないよう、
# 同じ掃除を通しておく。掃除に使う文言は**こちらにしか無いもの**だけ
# （汎用語を入れると他 MOD やゲームのボタンを巻き込む。`302_` の項を参照）。
OUR_LABELS = (ROAD_LABEL,)

# 押下を横取りするための印。**他の MOD と別のキーにすること。**
MARK = "mod_road_action"

# ---------------------------------------------------------------- 設定（mod.json）
# ここの定数だけが GUI から変えられる（ローダは入口モジュールのグローバルへ
# 書き込む）。他のファイルへ移さないこと。
#
# 難易度の決め方。
#   "between"     移動元と移動先の間から一様に選ぶ（既定）
#   "destination" 移動先に合わせる
#   "harder"      高いほうに合わせる
DIFFICULTY_MODE = "between"

# 選んだ難易度に足す下駄。危険な道を素の依頼より重くしたいときに使う。
DIFFICULTY_OFFSET = 0

# 危険な道1回にかける日数の上限。**道中のクエストと最後の移動の合計。**
# 既定 14 は馬車と同じ（実測の確認画面が `馬車(1000G)`＝14日）。
# 0 なら日数を1日も進めない。365 にすれば実質「上限なし」。
TRAVEL_DAYS = 14

# 踏破した後、実際の移動に**どのボタンの `args` を借りるか**。
#   "walk"     徒歩（既定。所持金が要らない。日数は上の上限で切り詰める）
#   "carriage" 馬車（所持金が要る。足りないときの挙動は未確認）
ARRIVAL_MODE = "walk"

# 体力（スタミナ）がこの割合を下回っていたら道を断る。既定 33 ＝ 3分の1。
# 0 にすると体力を見ない（いつでも行ける）。
STAMINA_MIN_PERCENT = 33

# 到着の移動中、ゲームが出す「徒歩で目指す。長旅だ...」を伏せるか。
HIDE_TRAVEL_TEXT = True

# 道の険しさ（難易度）を出発時に画面へ出すか。
ANNOUNCE_DIFFICULTY = True

# 依頼概要（`request_summary`）の末尾に「クリアすると移動する」と書き足すか。
# 掲示板に残った道を後から受けたときにも、それが移動の依頼だと分かる。
NOTE_IN_SUMMARY = True

# ---------------------------------------------------------------- コード側の設定
# `AreaMoveManager` の `mode` の実測値。**実機で観測できたものだけ書くこと**
# （GAME.md §2.18）。空にしてもボタンの文字列 → 並び順、の順で下がるので動く。
# 観測した対は毎回
# ログに出るので、ゲームの更新で変わったらここを書き直す。
WALK_MODES = ("on_foot",)
CARRIAGE_MODES = ("coach",)

# 文字列で見分けるときの手掛かり（`mode` が当たらないときだけ使う）。
WALK_WORDS = ("徒歩", "歩")
CARRIAGE_WORDS = ("馬車", "馬")

# 到着の移動中だけ伏せる文言（`HIDE_TRAVEL_TEXT`）。
#
# `徒歩で目指す。長旅だ...` は実測値。馬車側の文言は未実測なので、当たらなければ
# 何も起きないというだけの当て。
# **伏せるのはこちらが起こした移動の最中だけ**で、普通の徒歩・馬車の移動や
# 到着の合図（`辿り着いた。`）・待機表示の点には触らない。
MUTED_MOVE_TEXTS = ("徒歩で目指す", "長旅だ", "馬車で目指す")

# 画面に出す文言。
REFUSE_TEXT = "この道を行くには、今は体力が無い。"
REFUSE_DETAIL = "（体力 {value}/{limit} ― 休むか、医者にかかるかだ）"
LOOKING_TEXT = "――{target}へ抜ける道の話を聞いている……"
FOUND_TEXT = "「{title}」――{target}へ抜ける道は、そう呼ばれている。"
DANGER_TEXT = "（この道のりの危険度: {difficulty} ／ かかる日数: 最大{days}日）"
ARRIVE_TEXT = "――道は抜けた。{target}が見えてくる。"
RETIRE_TEXT = "危険な道を引き返した。{origin}に留まっている。"
NO_ROAD_TEXT = "（その道は今は見つからない）"
NO_QUEST_TEXT = "（道の話はまとまらなかった）"

# 依頼概要に書き足す一文（`NOTE_IN_SUMMARY`）。**LLM が書いた文の末尾に足す**
# だけで、生成された本文には手を入れない。
#
# `ARRIVAL_NOTE_MARK` は「もう足してある」の目印。生成の直後は片方の格納先に
# しか居ないことがあるので受注の時点でもう一度足しに行く（GAME.md §2.9）。
# 目印で見るので、何度呼んでも二重にならない。**紐付けが切れたら消す**
# （`drop_road`）― もう移動しない依頼が「移動します」と言い続けないように。
ARRIVAL_NOTE = "\n\n※このクエストをクリアすると「{target}」に移動します。"
ARRIVAL_NOTE_MARK = "※このクエストをクリアすると"

# `QuestChoiceManager(app, quest_type, quest_id)` の `quest_type`。
# **`world.quests` に対して通るのはこれだけ**（`206_` の総当たりで確定。
# GAME.md §2.9）。他の値は `story_quests` 側の分岐に落ちて `KeyError` になる。
QUEST_TYPE = "settlement_quest"

# 生成の印の寿命（秒）。押してから生成が始まるまでの間に別の生成が割り込んだ
# 場合に、そちらへ付いてしまうのを防ぐ。
INJECT_TTL = 300.0

# 控えの寿命（秒）。これを過ぎた道中は忘れる（7日）。
PENDING_TTL = 7 * 24 * 3600.0

# 最後の移動を起こしてから控えを外すまでの実時間（秒）。
#
# 移動は `process_choice` に渡した先の別スレッドで走るので、**渡した直後に
# 控えを消すと日数の切り詰めが間に合わない**。かといって残したままにすると、
# 移動が失敗したときに無関係な日数送り（訓練・休養）まで切り詰め続ける。
# 目的地に着いたことが確認できたらすぐ外し、確認できなくてもここで必ず外す。
MOVE_TIMEOUT = 300.0

# 生成・完了の後、画面を触るまでの落ち着き待ち。
SETTLE = 0.4

# 難易度の下限。ゲーム側の上限は分からないので、上は抑えない。
MIN_DIFFICULTY = 1

# 集落の画面と見なす目印。**文字列ではなく spec のクラス名で見る。**
#
# `MovePhaseManager` が要る。帰還先はエリアの**入口**で、そこの選択肢は
# 隣の施設への移動だけ。
SETTLEMENT_MARKS = ("MovePhaseManager", "DisplayTalkChoice", "DisplayAreaMoveChoice")

# 乱数は MOD 専用のものを使う。グローバルの `random` から引くとゲーム自身の
# 乱数列がずれる（TECH.md §6.1）。
_RNG = random.Random()


def road_brief(origin_name, target_name, difficulty):
    """生成プロンプトの `area_description` に足す文。

    引数を足すのではなく既存の自由記述欄に載せるだけなので、出力スキーマ
    （`QuestStructure`）にも呼び出し側にも影響しない（`301_` と同じ手口）。
    """
    origin = origin_name or "出発地"
    target = target_name or "目的地"
    return (
        "\n\n【この依頼の性質 — 最優先で反映すること】\n"
        "これは町で受ける雑事ではなく、**「{origin}」から「{target}」へ抜ける"
        "危険な道のり、その旅路そのもの**である。\n"
        "- 舞台は2つの土地を繋ぐ道中（街道・峠道・古い地下道・森・湿地・廃墟など）"
        "とすること。町の中を舞台にしてはならない。\n"
        "- area は、その道中を進んでいく一本道として設計すること。\n"
        "- quest_title は「{origin}」から「{target}」への道を行くことが分かるもの"
        "とすること。\n"
        "- request_summary は「危険な道を抜けて『{target}』へ到達する」ことを"
        "目的として書くこと。\n"
        "- boss は、その道を塞いでいる存在（主・群れの長・崩落を招いた何か）"
        "とすること。討ち払えば道が通れるようになる、という筋にすること。\n"
        "- client_statement は、道行きを勧める（あるいは止める）土地の者の言葉"
        "とすること。\n"
        "- この道のりの危険度は {difficulty} である。それに見合う相手を配置すること。"
    ).format(origin=origin, target=target, difficulty=difficulty)


def roll_difficulty(origin_level, target_level):
    """移動元と移動先の間から1つ選ぶ。"""
    low, high = sorted((int(origin_level), int(target_level)))
    if DIFFICULTY_MODE == "destination":
        value = int(target_level)
    elif DIFFICULTY_MODE == "harder":
        value = high
    else:
        value = _RNG.randint(low, high)
    return max(MIN_DIFFICULTY, value + int(DIFFICULTY_OFFSET))


def apply(ctx):
    log_path = ctx.out_path(LOG_BASENAME)

    def write(text):
        try:
            with open(log_path, "a", encoding="utf-8") as fh:
                fh.write("[{}] {}\n".format(
                    datetime.datetime.now().isoformat(timespec="milliseconds"), text))
        except Exception:
            ctx.log_exc("road travel: write failed")

    screen = ui.Screen(ctx, write, tag="road travel", mark=MARK)

    # 道中の控え。注入し直しても前の層が書いたものを引き継ぐ（`journey.py`）。
    journey = Journey(ctx.out_path(STATE_BASENAME), write,
                      ttl=PENDING_TTL, move_timeout=MOVE_TIMEOUT)
    journey.reload()

    state = {
        "inject": None,      # random_quest_generator への差し込み（1回で使い切る）
        "inject_at": 0.0,
        "generating": False,
        "departing": False,  # 自分が起こした AreaMoveManager かどうかの印
        # 確認画面で読み取ったゲーム自身の移動ボタン（`(entry, args)` の一覧）。
        # 押下の処理はここから `args` を写す ― 値を発明しないため。
        "options": None,
    }

    def road_of(app, *stages):
        """いま有効な控え。段階が合わなければ None。"""
        return journey.current(world.world_key(app) if app is not None else None,
                               stages)

    def drop_road(app, why, clear_note=True):
        """道の紐付けを外す。**依頼概要に足した一文もここで消す。**

        紐付けが切れた依頼は普通の討伐依頼として終わるので、「クリアすると
        移動します」が残っていると嘘になる。踏破して着いた後（`arrived`）だけは
        消さない。あの一文はその時点では本当だったから。

        取りこぼす場合が1つある: 控えの寿命（`PENDING_TTL`、7日）で消えたとき。
        その経路は控えを読む前に落とすので、どのクエストの話だったか分からない。
        7日のあいだ別の依頼も移動も1度もしなければ、という条件なので実際には
        起きにくい。
        """
        record = journey.record
        if record is None:
            return False
        if clear_note and app is not None and record.get("quest_id"):
            removed = world.strip_quest_value(
                app, str(record["quest_id"]), "request_summary",
                ARRIVAL_NOTE_MARK,
                on_error=lambda msg: write("WARN note: " + msg))
            if removed:
                write("note: removed the arrival line from quest {!r} "
                      "({} store(s))".format(record["quest_id"], removed))
        return journey.drop(why)

    def settle(app, then):
        screen.when_idle(app, then, settle=SETTLE, proceed_on_timeout=True,
                         tag="settle")

    # ============================================================ 断る条件
    def stamina_refusal(app):
        """体力が足りずに道を断るなら出す文言。行けるなら None。

        **読めなかったときは通す。** 値を読めないことを理由に遊びを止めない
        （そのときは WARN を残すので、原因は後から追える）。
        """
        percent = max(0, min(100, int(STAMINA_MIN_PERCENT)))
        if percent <= 0:
            return None
        value, limit = world.stamina_of(app)
        if value is None:
            write("WARN stamina: cannot read physical_integrity; "
                  "letting the road through")
            return None
        write("stamina: {}/{} ({:.0f}%) threshold={}% exhausted={!r}".format(
            value, limit, 100.0 * value / limit, percent,
            getattr(getattr(app, "player", None), "exhausted", "<missing>")))
        if value * 100 >= limit * percent:
            return None
        return REFUSE_DETAIL.format(value=int(value), limit=int(limit))

    # ============================================================ 出発する
    def start_road(app, choice_text):
        """「危険な道を行く」が押されたとき。道中のクエストを1件作って受注へ。"""
        if state["generating"]:
            screen.say(app, "……いま道の話を聞いているところだ。")
            return
        # 体力が足りないなら**ここで断る**。生成（LLM で数十秒〜数分）にも
        # 世界のクエスト一覧にも一切触らない。
        refusal = stamina_refusal(app)
        if refusal is not None:
            write("refused: not enough stamina {}".format(refusal))
            screen.say(app, REFUSE_TEXT)
            screen.say(app, refusal)
            # 確認画面はそのまま残っている。塗り直して徒歩・馬車を選べる状態に戻す。
            screen.apply_buttons(app, None, "refused")
            return

        options = state["options"] or world.move_options(getattr(app, "buttons", None))
        state["options"] = None
        if not options:
            write("start: no AreaMoveManager button on this screen; aborting")
            screen.say(app, NO_ROAD_TEXT)
            return
        entry, args, how = world.pick_option(
            options,
            WALK_MODES if ARRIVAL_MODE == "walk" else CARRIAGE_MODES,
            WALK_WORDS if ARRIVAL_MODE == "walk" else CARRIAGE_WORDS,
            first=(ARRIVAL_MODE == "walk"))

        target_id = str(args[0])
        target = world.area_of(app, target_id)
        origin = ui.current_area(app)
        origin_id = ui.area_id_of(origin)
        target_name = world.area_name(target, "その土地")
        origin_name = world.area_name(origin, "この土地")

        origin_level = world.area_level(app, origin, write,
                                        "origin {!r}".format(origin_name),
                                        MIN_DIFFICULTY)
        target_level = world.area_level(app, target, write,
                                        "target {!r}".format(target_name),
                                        MIN_DIFFICULTY)
        difficulty = roll_difficulty(origin_level, target_level)

        write("=" * 78)
        write("start: {!r}({}) -> {!r}({}) mode={!r} via {} label={!r}".format(
            origin_name, origin_id, target_name, target_id, args[1], how,
            entry.get("text")))
        write("start: levels origin={} target={} -> difficulty={} (mode={} offset={})"
              .format(origin_level, target_level, difficulty,
                      DIFFICULTY_MODE, DIFFICULTY_OFFSET))

        quest_id, quest = generate(app, origin_name, target_name, difficulty)
        if quest_id is None:
            return

        # 前の道が残っていたら、ここで紐付けと一文を外す。上書きするだけだと、
        # もう移動しない依頼が「移動します」と言い続けることになる。
        if journey.record is not None:
            drop_road(app, "replaced by a new road")

        journey.start({
            "stage": "offered",
            "quest_id": str(quest_id),
            "cls_name": "AreaMoveManager",
            "args": [str(a) for a in args],
            "label": entry.get("text") or ROAD_LABEL,
            "target_area_id": target_id,
            "target_area_name": target_name,
            "origin_area_id": origin_id,
            "origin_area_name": origin_name,
            "difficulty": difficulty,
            "world": world.world_key(app),
            "at": time.time(),
            # 道中と最後の移動で使った日数の合計（`TRAVEL_DAYS` の予算）。
            "days_spent": 0,
            "moving_at": 0.0,
        })

        title = world.short(world.quest_value(quest, "quest_title", ""), 40)
        # `restore=False` ＝ この後すぐ受注画面を開くので、元の選択肢は塗り直さない
        # （塗ると一瞬だけ古い画面が見える。`301_` の教訓）。
        screen.busy_off(app, restore=False)
        settle(app, lambda: open_acceptance(app, quest_id, title, target_name,
                                            difficulty))

    def generate(app, origin_name, target_name, difficulty):
        """ゲーム自身の生成経路を、道中の性質を添えて呼ぶ。`(id, quest)`。

        **別スレッドに投げない。ここで最後までやりきる**（`301_` の教訓）。
        `process_choice` は `execute` が返るまで待機表示を出しっぱなしにする。
        """
        display_cls = ui.cls_of("DisplayQuestChoice")
        if display_cls is None:
            write("start: DisplayQuestChoice not found")
            screen.say(app, NO_ROAD_TEXT)
            return None, None

        state["generating"] = True
        state["inject"] = {"origin": origin_name, "target": target_name,
                           "difficulty": difficulty}
        state["inject_at"] = time.monotonic()
        screen.busy_on(app)
        screen.say(app, LOOKING_TEXT.format(target=target_name))

        started = time.monotonic()
        before = set(world.quest_ids(app))
        try:
            display_cls(app).generate_random_quest()
        except Exception:
            ctx.log_exc("road travel: generate_random_quest failed")
            state["generating"] = False
            state["inject"] = None
            screen.busy_off(app)
            settle(app, lambda: screen.say(app, NO_QUEST_TEXT))
            return None, None
        added = sorted(set(world.quest_ids(app)) - before, key=world.id_sort)
        state["generating"] = False
        state["inject"] = None
        write("start: took {:.1f}s; new quest ids={}".format(
            time.monotonic() - started, added))

        if not added:
            screen.busy_off(app)
            settle(app, lambda: screen.say(app, NO_QUEST_TEXT))
            return None, None

        quest_id = added[-1]
        write_difficulty(app, quest_id, difficulty, first=True)
        note_arrival(app, quest_id, target_name)
        return quest_id, world.quest_of(app, quest_id)

    def note_arrival(app, quest_id, target_name):
        """依頼概要の末尾に「クリアすると移動する」を足す。

        **LLM が書いた文には手を入れず、後ろに1行足すだけ。** 掲示板に残った
        道を後から受けたときにも、それが移動の依頼だと分かるようにするため。
        目印で見るので何度呼んでも二重にならない（`world.append_quest_value`）。
        """
        if not NOTE_IN_SUMMARY or not target_name:
            return 0
        written = world.append_quest_value(
            app, str(quest_id), "request_summary",
            ARRIVAL_NOTE.format(target=target_name), mark=ARRIVAL_NOTE_MARK,
            on_error=lambda msg: write("WARN note: " + msg))
        if written:
            write("note: added the arrival line to request_summary "
                  "({} store(s), target={!r})".format(written, target_name))
        return written

    def write_difficulty(app, quest_id, difficulty, first=False):
        """選んだ難易度を**両方の格納先**へ書く（片方だけだと表示と保存がずれる）。

        生成した直後は `world_dict['quests']` にまだ現れていないことがある。
        だから受注の時点でもう一度書く。
        同じ値を書くだけなので何度でも安全。
        """
        quest = world.quest_of(app, quest_id)
        before = world.quest_value(quest, "difficulty", None)
        stores = len(world.quest_stores(app))
        written = world.set_quest_value(
            app, str(quest_id), "difficulty", difficulty,
            on_error=lambda msg: ctx.log_exc("road travel: " + msg))
        if first:
            write("start: quest {!r} {!r} difficulty {!r} -> {} ({} store(s))".format(
                quest_id, world.short(world.quest_value(quest, "quest_title", ""), 40),
                before, difficulty, written))
        else:
            write("armed: difficulty {} written to {} store(s)".format(
                difficulty, written))
        if not written:
            write("WARN: could not write the difficulty; "
                  "the road keeps the game's own value")
        elif first and written < stores:
            write("start: only {} of {} store(s) had the quest yet; "
                  "will write it again when the road is accepted".format(
                      written, stores))
        return written

    def open_acceptance(app, quest_id, title, target_name, difficulty):
        """ゲーム本来の受注画面へ渡す。落ちたら掲示板へ下がる。"""
        screen.say(app, FOUND_TEXT.format(title=title or "名も無い道",
                                          target=target_name))
        if ANNOUNCE_DIFFICULTY:
            screen.say(app, DANGER_TEXT.format(difficulty=difficulty,
                                               days=max(0, int(TRAVEL_DAYS))))
        choice_cls = ui.cls_of("QuestChoiceManager")
        if choice_cls is not None:
            try:
                manager = choice_cls(app, QUEST_TYPE, str(quest_id))
            except Exception:
                # `quest_type` の語彙が変わったときはここに来る（`301_` が
                # 一度これでゲームを落としている）。掲示板なら語彙を知らずに済む。
                ctx.log_exc("road travel: QuestChoiceManager({!r}, {!r}) failed"
                            .format(QUEST_TYPE, quest_id))
                manager = None
            if manager is not None:
                write("acceptance: process_choice(QuestChoiceManager, {!r})".format(
                    quest_id))
                screen.start_phase(app, manager, title or ROAD_LABEL)
                return
        display_cls = ui.cls_of("DisplayQuestChoice")
        if display_cls is None:
            write("acceptance: no way to open the quest; the road is still listed")
            return
        write("acceptance: falling back to the quest board")
        screen.start_phase(app, display_cls(app), "クエスト掲示板")

    class RoadPhase(object):
        """自前のフェーズ。**`PhaseSpec` には決して載せない**（セーブに焼かれる）。"""

        def __init__(self, app):
            self.app = app

        def execute(self, choice_text):
            try:
                start_road(self.app, choice_text)
            except Exception:
                ctx.log_exc("road travel: phase failed")

    # ============================================================ 段階を進める
    def observe_quest(app):
        """ゲームが道中のクエストを進めているなら、控えを本決まりにする。

        受注を `QuestStartManager` で捕まえる経路の**取りこぼしを埋める**。
        あちらは一瞬しか来ないので、その瞬間に注入し直されていると永久に
        armed にならない。こちらは画面が組み直されるたびに
        見るので、いつ入ってきても拾える。
        """
        record = road_of(app, "offered")
        if record is None or world.current_quest_id(app) != record.get("quest_id"):
            return False
        journey.advance("armed", at=time.time())
        write("armed: the game is running quest {!r}; {!r} is waiting at the end"
              .format(record.get("quest_id"), record.get("target_area_name")))
        return True

    def arrived_check(app):
        """目的地に着いていたら控えを外す（＝日数の切り詰めもここで終わる）。"""
        record = journey.record
        if record is None or record.get("stage") != "moving":
            return False
        if ui.area_id_of(ui.current_area(app)) != record.get("target_area_id"):
            return False
        write("arrived: {!r} reached; {} day(s) spent of {} allowed".format(
            record.get("target_area_name"), journey.days_spent(), TRAVEL_DAYS))
        if not journey.days_spent():
            # 上限が効いていない可能性そのもの。ここは黙って通さない。
            write("WARN arrived: elapse_days was never seen; "
                  "the {}-day limit did not apply to this build".format(TRAVEL_DAYS))
        # 着いた後は消さない ― あの一文はその時点では本当だったから。
        drop_road(app, "arrived", clear_note=False)
        return True

    def in_settlement(buttons):
        """いま集落の選択肢が出ているか。**文字列ではなく spec で見る。**"""
        if not isinstance(buttons, (list, tuple)):
            return False
        return any(ui.spec_cls_name(entry) in SETTLEMENT_MARKS for entry in buttons)

    # ============================================================ 到着する
    def depart(app):
        """控えたゲーム自身の `args` で移動を起こす。"""
        record = road_of(app, "ready")
        if record is None:
            return
        cls = ui.cls_of(record.get("cls_name") or "AreaMoveManager")
        if cls is None:
            write("arrive: AreaMoveManager not found; the road stays pending")
            return
        args = list(record.get("args") or [])
        if len(args) < 2:
            write("arrive: the recorded args are incomplete {!r}".format(args))
            drop_road(app, "incomplete args")
            return
        journey.advance("moving", moving_at=time.time())
        screen.say(app, ARRIVE_TEXT.format(
            target=record.get("target_area_name") or "目的地"))
        write("arrive: process_choice({}, {!r}) args={!r} days_spent={}".format(
            record.get("cls_name"), record.get("label"), args, journey.days_spent()))
        state["departing"] = True
        try:
            manager = cls(app, *args)
        except Exception:
            ctx.log_exc("road travel: cannot build the area move")
            state["departing"] = False
            drop_road(app, "area move failed")
            screen.say(app, "（道の先で行き方を見失った）")
            return
        try:
            screen.start_phase(app, manager, record.get("label") or ROAD_LABEL)
        finally:
            state["departing"] = False
        # **ここで控えを消さない。** 移動は `process_choice` の先の別スレッドで
        # 走ることがあるので、消すと日数の切り詰めが間に合わない。目的地に
        # 着いたのを見てから外す（`arrived_check`。見えなくても `MOVE_TIMEOUT`）。
        arrived_check(app)

    # ================================================================ フック
    @ctx.wrap("__main__:AreaMoveCofirmation.update_button_display", required=False)
    def confirmation_buttons(orig, self, *args, **kwargs):
        """徒歩・馬車が並び終えた**後**に「危険な道を行く」を1つ足す。

        ゲームが作った移動のボタンには一切触らない。読むだけ（`args` を控える）。
        """
        result = orig(self, *args, **kwargs)
        try:
            app = getattr(self, "app", None) or ui.find_app()
            if app is None:
                return result
            buttons = getattr(app, "buttons", None)
            if not isinstance(buttons, list):
                write("confirm: app.buttons is {}; leaving it alone".format(
                    type(buttons).__name__))
                return result
            options = world.move_options(buttons)
            if not options:
                return result       # 移動の確認画面ではない（or 構成が変わった）
            write("confirm: options {}".format(
                [(entry.get("text"), opts) for entry, opts in options]))
            state["options"] = options
            # **印を失った自前ボタンの残骸を先に落とす**（`ui.Screen.prune_stale`）。
            # 移動の確認画面だと分かってから（`options` が取れてから）呼ぶ。
            screen.prune_stale(buttons, OUR_LABELS)
            if any(screen.mark_of(entry) for entry in buttons):
                return result       # 既に在る（開き直しても二重にしない）
            entry = screen.button(ROAD_LABEL, mark="road")
            if entry is None:
                write("confirm: cannot build the button (PhaseSpec unavailable)")
                return result
            # 「やめる」の手前。無ければ末尾。
            at = len(buttons)
            for index, item in enumerate(buttons):
                if ui.spec_cls_name(item) == ui.SAFE_CLS:
                    at = index
                    break
            buttons.insert(at, entry)
            # ここはゲームが並べ終えた直後 ― 描画はもう済んでいるので、次の
            # フレームで塗り直す（`app.buttons` は直接いじったので entries=None）。
            screen.apply_buttons(app, None, "confirm")
        except Exception:
            ctx.log_exc("road travel: cannot add the road button")
        return result

    @ctx.wrap("__main__:InstantaleApp.on_button_press", required=False)
    def on_button_press(orig, self, button_index, *args, **kwargs):
        """自前のボタンだけ横取りする。印が無ければ必ず素通し。"""
        entry = ui.pressed_entry(self, button_index)
        action = screen.mark_of(entry)
        if action is None:
            return orig(self, button_index, *args, **kwargs)
        text = (entry.get("text") if isinstance(entry, dict) else None) or ROAD_LABEL
        write("pressed {!r} ({})".format(text, action))
        screen.start_phase(self, RoadPhase(self), text,
                           fallback=lambda: start_road(self, text))
        return None

    @ctx.wrap("__main__:InstantaleApp.refresh_choice_buttons", required=False)
    def refresh_choice_buttons(orig, self, reset_page=False, *args, **kwargs):
        """集落の選択肢に戻ったら、待っている移動を起こす（**保険の経路**）。

        本来は完了した瞬間に動く（`quest_end`）。ここが働くのは、完了の瞬間を
        捉えられなかったとき。ゲームを再起動して控えだけが残っている場合や、
        `when_idle` が待ちきれずに取り消された場合。
        """
        try:
            journey.sync()
            observe_quest(self)
            arrived_check(self)
            record = road_of(self, "ready")
            if record is not None and in_settlement(getattr(self, "buttons", None)):
                journey.advance("arriving")
                write("arrive: back in a settlement; leaving for {!r}".format(
                    record.get("target_area_name")))

                def go():
                    # 読み直しで入れ物が入れ替わっていることがあるので、
                    # 掴んだ参照ではなく今の控えを見る。
                    if journey.record is not None and \
                            journey.record.get("stage") == "arriving":
                        journey.advance("ready")   # depart() が読む段階に戻す
                    depart(self)

                screen.when_idle(self, go, settle=SETTLE, proceed_on_timeout=True,
                                 tag="arrive")
        except Exception:
            ctx.log_exc("road travel: cannot start the pending move")
        return orig(self, reset_page, *args, **kwargs)

    @ctx.wrap("__main__:QuestStartManager.__init__", required=False)
    def quest_start(orig, self, app, quest_type, quest_id, *args, **kwargs):
        """道中のクエストが実際に始まったら控えを本決まりにする。

        受注しなかった（「やめる」を押した）道は、ここに来ないので `offered` の
        まま残り、別のクエストが始まった時点で落ちる。

        **この経路だけに頼らない。** 受注の瞬間に注入し直されていると、この層は
        まだ控えを読めていない。`observe_quest` が
        `app.current_quest_data` を見て後から埋める。
        """
        try:
            journey.sync()
            record = road_of(app, "offered", "armed")
            if record is not None:
                if str(quest_id) == record.get("quest_id"):
                    journey.advance("armed", at=time.time())
                    write("armed: quest {!r} started; {!r} is waiting at the end"
                          .format(quest_id, record.get("target_area_name")))
                    difficulty = record.get("difficulty")
                    if isinstance(difficulty, int):
                        write_difficulty(app, quest_id, difficulty)
                    # 生成の直後に片方の格納先しか無かったぶんをここで埋める。
                    note_arrival(app, quest_id, record.get("target_area_name"))
                else:
                    drop_road(app, "another quest started ({})".format(quest_id))
        except Exception:
            ctx.log_exc("road travel: cannot arm the road")
        return orig(self, app, quest_type, quest_id, *args, **kwargs)

    @ctx.wrap("__main__:QuestEndManager.execute", required=False)
    def quest_end(orig, self, *args, **kwargs):
        """踏破。帰還処理が済んだ**その場で**移動する。

        戦利品（`LootPhaseManager`）はこれより前なので、ここで動かしても
        取りこぼすものは無い（GAME.md §2.9）。報酬・才能の
        テキストを流している最中には割り込まないよう `when_idle` を通す。
        """
        app = getattr(self, "app", None) or ui.find_app()
        # **どのクエストが終わるのかは `orig` を呼ぶ前に読む**（終わった後では
        # `current_quest_data` が片付いている）。
        ended = None
        try:
            journey.sync()
            ended = world.current_quest_id(app)
        except Exception:
            ctx.log_exc("road travel: cannot read the current quest")
        result = orig(self, *args, **kwargs)
        try:
            record = road_of(app, "offered", "armed")
            # id で一致したなら段階は問わない。`armed` を取りこぼしていても、
            # 終わったのが道中のクエストであることはゲーム自身が言っている。
            if record is not None and (ended == record.get("quest_id")
                                       or (ended is None
                                           and record.get("stage") == "armed")):
                journey.advance("ready", at=time.time())
                write("cleared: quest {!r} ended; the road to {!r} is open".format(
                    ended or record.get("quest_id"), record.get("target_area_name")))
                if app is not None:
                    settle(app, lambda: depart(app))
            elif record is not None:
                write("quest {!r} ended but the road is for {!r}; not moving".format(
                    ended, record.get("quest_id")))
        except Exception:
            ctx.log_exc("road travel: cannot mark the road as cleared")
        return result

    @ctx.wrap("__main__:QuestRetireManager.execute", required=False)
    def quest_retire(orig, self, *args, **kwargs):
        """放棄。**移動しない**。危険な道を引き返した、が正しい。"""
        app = getattr(self, "app", None) or ui.find_app()
        ended = None
        try:
            journey.sync()
            ended = world.current_quest_id(app)
        except Exception:
            ctx.log_exc("road travel: cannot read the current quest")
        result = orig(self, *args, **kwargs)
        try:
            record = road_of(app, "offered", "armed", "ready")
            if record is not None and ended is not None \
                    and ended != record.get("quest_id"):
                write("quest {!r} was abandoned but the road is for {!r}; "
                      "leaving it alone".format(ended, record.get("quest_id")))
            elif record is not None and (ended is not None
                                         or record.get("stage") != "offered"):
                origin = record.get("origin_area_name") or "元の土地"
                drop_road(app, "the quest was abandoned")
                if app is not None:
                    settle(app, lambda: screen.say(
                        app, RETIRE_TEXT.format(origin=origin)))
        except Exception:
            ctx.log_exc("road travel: cannot cancel the road")
        return result

    @ctx.wrap("__main__:InstantaleApp.add_text", required=False)
    def add_text(orig, self, context, *args, **kwargs):
        """到着の移動中だけ、ゲームの「徒歩で目指す。長旅だ...」を伏せる。

        危険な道は道中をクエストとして踏破しているので、そこから改めて
        長旅だと言われると筋が合わない（出発の一言はこちらが出している）。

        **伏せる窓は自分が起こした移動の最中（`moving`）だけ**で、しかも
        名指しした文言に限る。普通の徒歩・馬車の移動、到着の合図
        （`辿り着いた。`）、待機表示の点はそのまま通す。
        """
        try:
            record = journey.record
            if HIDE_TRAVEL_TEXT and isinstance(context, str) \
                    and isinstance(record, dict) \
                    and record.get("stage") == "moving" \
                    and any(word in context for word in MUTED_MOVE_TEXTS):
                write("muted while arriving: {!r}".format(context))
                return None
        except Exception:
            ctx.log_exc("road travel: cannot filter the travel text")
        return orig(self, context, *args, **kwargs)

    @ctx.wrap("__main__:InstantaleApp.elapse_days", required=False)
    def elapse_days(orig, self, days, *args, **kwargs):
        """危険な道を行っている間だけ、日数の合計を `TRAVEL_DAYS` で頭打ちにする。

        **渡す数を減らすだけ**で、暦の進め方も日次処理もゲームのまま
        （`orig` は必ず呼ぶ。呼ばずに戻ると、日数以外の後始末まで落とすことになる）。
        道を行っていないときは1バイトも触らない。
        """
        try:
            journey.sync()
            observe_quest(self)
            record = road_of(self, "armed", "ready", "arriving", "moving")
            if record is not None and isinstance(days, (int, float)) \
                    and not isinstance(days, bool) and days > 0:
                budget = max(0, int(TRAVEL_DAYS))
                granted = journey.spend(days, budget)
                if granted != days:
                    write("days: {} -> {} (spent {}/{} on the road to {!r})".format(
                        days, granted, journey.days_spent(), budget,
                        record.get("target_area_name")))
                else:
                    write("days: +{} (spent {}/{})".format(
                        granted, journey.days_spent(), budget))
                return orig(self, granted, *args, **kwargs)
        except Exception:
            ctx.log_exc("road travel: cannot cap the days")
        return orig(self, days, *args, **kwargs)

    @ctx.wrap("__main__:AreaMoveManager.__init__", required=False)
    def area_move(orig, self, app, target_area_id, mode, *args, **kwargs):
        """自分が起こしたのでない移動が始まったら、待っている道は捨てる。

        普通に徒歩・馬車で移動してしまったなら、その道はもう関係が無い。
        """
        try:
            journey.sync()
            if not state["departing"] and road_of(app) is not None:
                drop_road(app, "the player travelled by other means "
                          "(mode={!r})".format(mode))
        except Exception:
            ctx.log_exc("road travel: cannot clear the road")
        return orig(self, app, target_area_id, mode, *args, **kwargs)

    # ---------------------------------------- 生成プロンプトに道中の性質を差し込む
    @ctx.wrap("scripts.llm.llm_manager_world_generate:random_quest_generator",
              required=False)
    def random_quest_generator(orig, world_overview, settlement_name,
                               settlement_overview, settlement_structure_description,
                               area_description, quest_difficulty, *args, **kwargs):
        """`area_description` に道中の性質を、`quest_difficulty` に選んだ値を渡す。

        印は1回で使い切るので、掲示板から普通に作られた依頼は素通しする。
        """
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

    ctx.log("area move dungeon: difficulty={} offset={} days<={} stamina>={}% "
            "arrival={} log={}".format(DIFFICULTY_MODE, DIFFICULTY_OFFSET,
                                       TRAVEL_DAYS, STAMINA_MIN_PERCENT,
                                       ARRIVAL_MODE, log_path))
