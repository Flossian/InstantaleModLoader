# -*- coding: utf-8 -*-
"""機能追加: 施設に着くと、その場の NPC の方から会話を始めてくる。

宿屋に入れば主人が、店に入れば店主が声をかけてくる。
「プレイヤーの行動をトリガーにしたイベント」。

## 2つのモード

    EVENT_MODE = "conversation"   ゲーム本来の会話フェーズを開始する（既定）
    EVENT_MODE = "narration"      情景描写に NPC のセリフを1行足すだけ

conversation は、プレイヤーが「会話する」→
NPC を選んだときと同じ経路をこちらから起こす。
したがって立ち絵の表示・会話履歴・関係値の更新・会話の終了処理は全てゲーム本来の実装がそのまま動く。
ゲーム本来の経路（GAME.md §2.5）:

```
process_choice(DisplayTalkChoice,        choice_text='会話する')
process_choice(ConversationStartManager, choice_text='マナ')      ← NPC を選んだ瞬間
process_choice(ConversationEndManager,   choice_text='会話を終了する')
```

つまり `app.process_choice(ConversationStartManager(app, npc_id), npc_name)` が「その
NPC のボタンを押した」に相当する。
`DisplayTalkChoice`（NPC 一覧の表示）は挟まない。
こちらが相手を決めているため。

narration は自前で LLM を1回呼んで情景描写に1行足すだけの軽い方。
立ち絵も会話モードも無いが LLM 1回で済む。
conversation が合わない場面用に用意してある。

## いつ発火させるか

`__main__:MovePhaseManager.move_phase` の復帰後。
この関数の構造（GAME.md §2.6）:

```
process_choice(MovePhaseManager, ...)    ボタン押下
  move_phase()
    narrator(...)                        ← 情景描写は move_phase の *内側* で呼ばれる
  move_phase 復帰                        ← ここで到着が確定している
```

narrator が **`move_phase` の内側**にあるので、
印を復帰後に置くと1手ずれる（回収するのは次の移動の narrator になる）。
narration モードでは印を `orig` の前に置いて、入れ子の narrator に回収させる。

会話フェーズの開始は、
移動の後始末（テキストの流し込み・ボタンの張り替え）が終わってからでないと噛み合わない。
そこで Kivy の Clock で `is_adding_text` / `is_button_enabled` を見張り、
手が空いた時点で押す。
これは「テキストが出終わってからプレイヤーがボタンを押す」のと同じ状況になる。

## その時点で読める情報（GAME.md §2.7）

    app.player.location      -> Facility   name / description / facility_type /
                                           owner / characters(その場の NPC id)
    app.world.characters     -> {id: Character}    Facility.owner はこの id

## 発生率

施設種別ごとに `CHANCE_INN` / `CHANCE_GUILD` /
… があり、**0 にすればその種別では出なくなる**。
`CHANCE_OVERRIDE` が None でなければそちらを全施設に使う（動作確認用）。
どれも `mod.json` から変えられる。
乱数はこの mod 専用の `random.Random` を使う。
グローバルから引くとゲーム自身の乱数列がずれるため（104_balance_area_bgm.py と同じ方針）。

**この % は「その施設に入った1回あたり」**の値。
同じ施設で続けて出ないように、発火した施設は
`COOLDOWN_VISITS` 回ぶん訪問を挟むまで抽選しない。

## 応答されない相手は黙る

こちらから始めた会話で、**プレイヤーが一言も返さないまま終わった**回数を
NPC ごとに数える。
`IGNORE_LIMIT` に達した相手は、その世界では二度と声をかけてこない。
途中で一言でも返せば控えは 0 に戻る。

    ConversationPhaseManager.conversation_continued   1ターン進んだ＝返事があった
    ConversationEndManager.finish_conversation        会話の終わり。ここで数える

止めるのは「NPC の方から声をかける」イベントだけで、
プレイヤーが自分から話しかける分には何も変わらない。
数えた結果は `state/` に世界ごと置く
（`out/` はログで消してよいもの、`state/` は遊びの続き）。

## ほかの MOD に譲る

施設に着いた直後に会話を始める MOD は、これ1本とは限らない。
同じ到着で2本が会話を始めると噛み合わないので、ローダの窓口 `arrivals` で分け合う。
話しかけると決めたら申し出（優先度 `ARRIVAL_PRIORITY`）、会話を始める直前に
自分より優先度の高い申し出があれば譲る。譲った回は間引きの回数（`COOLDOWN_VISITS`）に数えない。

分け合うのは conversation モードだけ。
narration モードのセリフは `move_phase` の内側（入れ子の narrator）で足すので、
ほかの MOD が申し出るより先に画面へ出ていて、譲りようがない。
そこで narration モードは申し出も勝者の確認もしない。
このため narration モードでは、足した1行とほかの MOD が始める到着の会話
（`911_` のライバルの声かけなど）が同じ到着で重なることがある。
申し出だけ出すと、同じ優先度で後から申し出たほかの MOD が、会話を始めないこちらに譲って黙ってしまう。

第一声の読み替えはローダの窓口 `arrivals.opening` に予約し、会話の1手と終わりは窓口 `talks` に預ける
（TECH.md §3.3.21）。相手の確かめは、会話を始めるときに渡した NPC の id で行う。
"""

import random
import sys
import time

from instantale_modloader import arrivals, frames, llm, modfacility, talks, ui
from instantale_modloader.state import WorldStore, process_store, world_key

LOG_BASENAME = "player_events.log"

#: ローダの窓口 `arrivals` に名乗る名前と優先度。0 は「ほかに話しかける MOD が居れば譲る」。
ARRIVAL_OWNER = "300_event_facility_arrival"
ARRIVAL_PRIORITY = 0

# "conversation"（立ち絵つきの会話フェーズ） / "narration"（1行だけ足す）
EVENT_MODE = "conversation"

# 施設種別ごとの発生率。
# 0 にするとその種別では出なくなる。
# ここに項目が無い種別（'ward' / 'location' / 'entrance' /
# 'exit' ＝ 主のいない通路）では発生しない。
# 表（dict）ではなく 1種別1定数 にしてあるのは、
# `mod.json` の "settings" で宣言できる型が
# bool/int/float/str/choice だけだから（config.py）。
# 辞書のままだと GUI から触れず、「プレイヤーの体験に関わる設定は
# mod.json に置く」方針から外れる。
# 施設種別はセーブの `facility_type` そのもので、
# ゲーム側で閉じた集合なので 1つずつ並べても増えない。
CHANCE_INN = 0.50
CHANCE_GUILD = 0.30
CHANCE_GENERAL_STORE = 0.25
CHANCE_SPECIALTY_SHOP = 0.25
CHANCE_BLACKSMITH = 0.15
CHANCE_MEDICAL_FACILITY = 0.20
CHANCE_ADMINISTRATIVE_OFFICE = 0.25
CHANCE_UNDERWORLD_OFFICE = 0.20

# None なら上の表を使う。
# 数値ならその確率を全施設種別に適用する（動作確認用）。
CHANCE_OVERRIDE = None

# 同じ施設で続けて出さない間隔。
# その施設に入った回数で数える。
# 2 なら「出た後、その施設に2回入るまでは出さない」（＝ 3回に1回まで）。
# 移動回数で数えると出入りするだけで抜けるので、その施設への訪問で数える（VERIFICATION.md §2.8）。
# 0 にすると間引かない（入るたびに抽選する）。
COOLDOWN_VISITS = 2

# 会話フェーズを開始する前に「手が空く」のを待つ。
IDLE_POLL = 0.3          # 見張りの間隔（秒）
IDLE_TIMEOUT = 30.0      # ここまで手が空かなければ諦める（移動が長引いている等）
IDLE_SETTLE = 0.6        # 手が空いてからさらに置く間（秒）。表示の余韻

# 会話の第一声を「NPC の方から声をかけた」に読み替える（conversation モードのみ）。
# ゲーム本来の文言は「プレイヤーが『話しかける』を実行しました」なので、
# そのままだとこちらから話しかけたことになってしまう。
REPHRASE_OPENING = True
REPHRASE_TTL = 120.0     # 読み替えの有効期限（秒）。取りこぼしたまま居座らせない

# narration モード用。
MANAGER_NAME = "mod_arrival_event"
MAX_TOKENS = 256
MAX_CHARS = 300
PENDING_TTL = 90.0

# セリフ1本にかける上限（秒）。
# 必ず渡す。
# ゲーム側の既定は無期限で、ここは narrator の中＝情景描写のスレッドなので、
# 返らないと画面ごと止まる。
LINE_TIMEOUT = 30.0

# 戦闘中・会話中など、イベントを出す前提が崩れている旗はローダの `ui.busy_flags`（`ui.BUSY_FLAGS`）で見る。
# 「手が空いているか」（テキストの流し込み中・操作を受け付けていない・ポップアップが開いている）の判定は
# `ui.IDLE_SIGNALS` 側にある。
# `in_shopping` は表に入っていない（店の外を往復するだけでも True のまま。理由は `ui.BUSY_FLAGS` の註）。
# 買い物窓が開いている状態は
# `is_popup_window_opened`（待っている間と会話を始める直前に見る。`gone`）で弾ける。

LANGUAGE_NAMES = {"japanese": "日本語", "english": "英語"}

# 応答されなかった回数の上限。
# この回数に達した NPC からは、もう話しかけない。
# こちらから始めた会話で、プレイヤーが一言も返さないまま終わったら1回と数える。
# 返事をすればその NPC の控えは 0 に戻る。
# 上限に達した相手はその世界では二度と声をかけてこない。
# 止めるのは「NPC の方から声をかける」イベントだけで、
# プレイヤーが自分から話しかける分には何も変わらない。
# 0 にすると数えるだけで止めない。
IGNORE_LIMIT = 2

# 数えた結果の置き場所（`state/<ここ>/<世界>.json`）。
# ログ（`out/`）ではなく `state/` なのは、消すと遊びの続きが巻き戻るため。
STATE_DIRNAME = "arrival_event"

#: 上の控え（`state.WorldStore`）を世代をまたいで1つだけ持つ置き場（`sys` の属性）。
#: `apply()` の中で作ると注入し直すたびにキャッシュと錠が別物になる（WorldStore の説明）。
STORE_ATTR = "_instantale_arrival_event_store"

# 会話の終わりを見届けられなかったときに、見届けの控えを捨てるまでの時間（秒）。
# 途中でゲームを閉じた場合などに、次の会話へ持ち越さないための保険。
WATCH_TTL = 1800.0

#: MOD のボタンで閉じた会話に付ける印（`ConversationEndManager` の実体に）。無視と数えない。
MOD_CLOSE_ATTR = "_instantale_300_mod_close"


def apply(ctx):
    log_path = ctx.out_path(LOG_BASENAME)
    # この mod 専用の乱数源。
    # ゲーム自身の乱数列に影響を与えないため。
    rng = random.Random()
    state = {
        "pending": None,      # narration モード: 入れ子の narrator が回収する印
        "visits": {},         # 施設 id -> その施設に入った回数
        "fired_at": {},       # 施設 id -> 発火したときの訪問回数
        "ignored_key": None,  # いま控えを持っている世界（`ignored_store`）
        "watching": None,     # こちらが始めた会話の見届け
    }

    write = ctx.logger(LOG_BASENAME)

    # 選択肢まわりと「手が空くのを待つ」は
    # `instantale_modloader.ui` に集約してある（`301_` / `302_` と共有）。
    # この mod で確立した「移動の後始末が終わってから押す」も、
    # 会話の終了処理の後始末に同じ形で効くので、そちらから使えるようにそこへ移した。
    screen = ui.Screen(ctx, write, tag="arrival event")
    find_app = ui.find_app

    # 施設種別ごとの発生率を表に組み直す。
    # **ここ（apply の中）で組むこと**。
    # 設定の反映はモジュールのグローバルへの書き込みで、
    # それが済むのは apply() を呼ぶ直前だから（config.apply_to_module）。
    # モジュールのトップレベルで組むと、選んだ値ではなく既定値の表になる。
    chance_table = {
        "inn": CHANCE_INN,
        "guild": CHANCE_GUILD,
        "general_store": CHANCE_GENERAL_STORE,
        "specialty_shop": CHANCE_SPECIALTY_SHOP,
        "blacksmith": CHANCE_BLACKSMITH,
        "medical_facility": CHANCE_MEDICAL_FACILITY,
        "administrative_office": CHANCE_ADMINISTRATIVE_OFFICE,
        "underworld_office": CHANCE_UNDERWORLD_OFFICE,
    }

    # ------------------------------------------------------------ 発火の判定
    def off_the_first_screen(app):
        """施設に着いたときの画面でなければ、その選択肢のクラス名を返す（画面なら None）。

        着いた先で衛兵に見つかると、ゲームは「大人しく捕まる／抵抗する！」だけを並べる。
        旗は何も立たないので `ui.busy_flags` では弾けず、その上から会話を始めていた（実機）。
        画面の見分けはローダの `modfacility.is_top_screen` に任せる（`331_` などと同じ判定）。
        """
        buttons = getattr(app, "buttons", None)
        if modfacility.is_top_screen(buttons):
            return None
        return [ui.spec_cls_name(entry) for entry in buttons]

    def chance_for(facility_type):
        if CHANCE_OVERRIDE is not None:
            # 表に無い種別（通路など）は override でも対象外のままにする。
            return CHANCE_OVERRIDE if facility_type in chance_table else 0.0
        return chance_table.get(facility_type, 0.0)

    def is_at(character, facility):
        """NPC の現在地が対象施設なら True を返す。

        現在地を読めない相手は施設の名簿を信じて True に倒す。
        名簿にしか居場所が無い NPC を弾くと、
        話者が一人も選ばれずイベント自体が出なくなる。
        """
        here = getattr(character, "location", None)
        if here is None:
            return True
        if here is facility:
            return True
        here_id = getattr(here, "id", None)
        facility_id = getattr(facility, "id", None)
        if here_id is None or facility_id is None:
            return True
        return here_id == facility_id

    # ------------------------------- 応答されなかった相手を覚えておく（世界ごと）
    #
    # 控えの出し入れ（場所・読み・キャッシュ・書き）は `state.WorldStore` に
    # 1つだけある。MOD ごとに写すとずれるため。
    store = process_store(STORE_ATTR, {"ignored": None})
    if store["ignored"] is None:
        # 器は無いときだけ作る（既定値に置くと、注入のたびに使わない器を作ることになる）。
        store["ignored"] = WorldStore(ctx, STATE_DIRNAME, write=write)
    ignored_store = store["ignored"].rebind(ctx, write)

    def ignores(app):
        """この世界の控えを返す。世界が変わったら読み直す。

        `state/` に置く（`out/` はログで、消してよいもの）。
        """
        key = world_key(app)
        if key is None:
            return {}
        state["ignored_key"] = key
        return ignored_store.load(key)

    def save_ignores():
        key = state["ignored_key"]
        if key is None:
            return
        ignored_store.save(key)

    def ignored_count(app, npc_id):
        entry = ignores(app).get(str(npc_id))
        if isinstance(entry, dict):
            try:
                return int(entry.get("count", 0))
            except (TypeError, ValueError):
                return 0
        return 0

    def is_muted(app, npc_id):
        """応答されなかった回数が上限に達した相手か。"""
        return bool(IGNORE_LIMIT) and ignored_count(app, npc_id) >= IGNORE_LIMIT

    def pick_speaker(app, facility):
        """施設に現在居る話者を (id, インスタンス) で返す。主がいれば主。

        応答されなかった回数が上限に達した相手は候補から外す。
        主が黙ったなら、その場に居る別の NPC が声をかける。
        """
        characters = getattr(getattr(app, "world", None), "characters", None)
        if not isinstance(characters, dict):
            return None, None
        npc_id, npc = ui.facility_owner(app, facility)
        if npc_id is not None and npc is not None and is_at(npc, facility) \
                and not is_muted(app, npc_id):
            return npc_id, npc
        # 施設に居合わせた NPC（重複することがあるので一意化してから選ぶ）。
        present = getattr(facility, "characters", None)
        if isinstance(present, (list, tuple)) and present:
            ids = sorted({
                str(cid) for cid in present
                if str(cid) in characters and is_at(characters[str(cid)], facility)
                and not is_muted(app, str(cid))
            })
            if ids:
                npc_id = rng.choice(ids)
                return npc_id, characters[npc_id]
        return None, None

    def decide(app):
        """発火条件を全て満たすなら (施設, NPC id, NPC) を返す。でなければ None。"""
        if app is None:
            return None

        # 「そもそも出しうる場所か」を先に見る。
        # 通路（対象外）での不発まで記録するとログが読めなくなるので、
        # ここまでは黙って落とす。
        facility = getattr(getattr(app, "player", None), "location", None)
        if facility is None:
            return None
        facility_id = str(getattr(facility, "id", ""))
        facility_type = getattr(facility, "facility_type", None)
        chance = chance_for(facility_type)
        if chance <= 0.0:
            return None
        where = "{} ({})".format(getattr(facility, "name", ""), facility_type)

        # ここまで来た＝この施設に入った。
        # 間引きはこの回数で数える。
        visits = state["visits"].get(facility_id, 0) + 1
        state["visits"][facility_id] = visits

        last = state["fired_at"].get(facility_id)
        if last is not None and visits - last <= COOLDOWN_VISITS:
            write("skip: {} on cooldown ({}/{} visit(s) since the last one)".format(
                where, visits - last, COOLDOWN_VISITS))
            return None

        # 状態の判定は施設が決まってから。
        # どこで何に邪魔されたのかが分かる。
        busy = ui.busy_flags(app)
        if busy:
            write("skip: {} busy {}".format(where, busy))
            return None
        other = off_the_first_screen(app)
        if other:
            write("skip: {} not the facility's first screen {}".format(where, other))
            return None
        if getattr(app, "current_quest_data", None):
            write("skip: {} in quest".format(where))
            return None

        roll = rng.random()
        if roll >= chance:
            write("skip: {} ({}) roll {:.2f} >= {:.2f}".format(
                getattr(facility, "name", ""), facility_type, roll, chance))
            return None

        npc_id, npc = pick_speaker(app, facility)
        if npc is None:
            write("skip: {} ({}) has nobody to speak".format(
                getattr(facility, "name", ""), facility_type))
            return None

        write("fire: {} ({}) roll {:.2f} < {:.2f} speaker={!r} id={!r}".format(
            getattr(facility, "name", ""), facility_type, roll, chance,
            getattr(npc, "name", ""), npc_id))
        state["fired_at"][facility_id] = visits
        if EVENT_MODE == "conversation":
            # narration モードは分け合いに加わらない（モジュール冒頭「ほかの MOD に譲る」）。
            arrivals.offer(app, ARRIVAL_OWNER, ARRIVAL_PRIORITY)
        return facility, npc_id, npc

    # ================================================================
    # conversation モード: ゲーム本来の会話フェーズを開始する
    # ================================================================
    def launch_conversation(app, facility, npc_id, npc):
        """「その NPC のボタンを押した」のと同じことをする。

        押すのは *今すぐ* ではない。
        移動の後始末（テキストの流し込み・ボタンの張り替え）の最中に割り込むと噛み合わないので、
        手が空くまで待つ。
        待ち合わせは Kivy の Clock で行う。
        ゲーム自身が UI を触るのと同じ土俵。
        """
        main = sys.modules.get("__main__")
        manager_cls = getattr(main, "ConversationStartManager", None) if main else None
        if manager_cls is None:
            write("launch: ConversationStartManager not found")
            arrivals.withdraw(app, ARRIVAL_OWNER)       # 始めない申し出は下げる（残すと後から申し出た MOD が譲って黙る）
            return
        npc_name = getattr(npc, "name", None)
        if not npc_name:
            write("launch: speaker has no name")
            arrivals.withdraw(app, ARRIVAL_OWNER)
            return

        def gone():
            """待ち合わせを取り消す条件。理由を返す（無ければ None）。"""
            if getattr(getattr(app, "player", None), "location", None) is not facility:
                return "player left {!r}".format(getattr(facility, "name", ""))
            busy = ui.busy_flags(app)
            if busy:
                return "became busy {}".format(busy)
            # 待っている間に売買の窓が開いた＝着いた後にもう手を付けている。閉じた後に声をかけても場面に合わない。
            if "is_popup_window_opened" in ui.busy_signals(app):
                return "a window was opened"
            # 手が空くまでの間に、衛兵のような別の画面に変わっていることもある。
            other = off_the_first_screen(app)
            if other:
                return "the screen changed to {}".format(other)
            return None

        # `when_idle` は取り消した回・待ちきれなかった回に `then`（start）を呼ばない。
        # 後始末（間引きと申し出を戻す）を start に置くと通らないので、cancel_if の中で行う。
        # 時間切れも同じ期限をこちらで見て取り消しにする。この期限は `when_idle` のものより先に来て、
        # `when_idle` は期限より先に cancel_if を見るので、待ちきれなかった回は必ずここを通る。
        deadline = time.monotonic() + IDLE_TIMEOUT
        dropped = []

        def cancelled():
            reason = gone()
            if reason is None and time.monotonic() > deadline and ui.busy_signals(app):
                reason = "gave up waiting for idle ({:.0f}s)".format(IDLE_TIMEOUT)
            if reason and not dropped:
                dropped.append(reason)
                write("launch: {}; cancelled".format(reason))
                # 出さなかった回は間引きに数えない（譲った回と同じ）。
                state["fired_at"].pop(str(getattr(facility, "id", "")), None)
                arrivals.withdraw(app, ARRIVAL_OWNER)
            return reason

        def start():
            # 待っている間に施設を出ていたら、もう始めない。
            if cancelled():
                return
            top = arrivals.winner(app)
            if top not in (None, ARRIVAL_OWNER):
                # 同じ到着でほかの MOD が先に話しかける。譲った回は間引きに数えない。
                write("launch: yielded to {} ({!r} does not speak)".format(top, npc_name))
                state["fired_at"].pop(str(getattr(facility, "id", "")), None)
                return
            if REPHRASE_OPENING:
                # 第一声を「NPC の方から声をかけた」に読み替える予約。
                reserve_opening(app, npc_id, getattr(facility, "name", ""))
            try:
                manager = manager_cls(app, npc_id)
            except Exception:
                ctx.log_exc("launch: cannot build ConversationStartManager")
                arrivals.cancel_opening(ARRIVAL_OWNER)
                arrivals.withdraw(app, ARRIVAL_OWNER)
                return
            write("launch: process_choice(ConversationStartManager, {!r}) npc_id={!r}"
                  .format(npc_name, npc_id))
            try:
                app.process_choice(manager, npc_name)
            except Exception:
                ctx.log_exc("launch: process_choice failed")
                arrivals.cancel_opening(ARRIVAL_OWNER)
                arrivals.withdraw(app, ARRIVAL_OWNER)
                return
            # ここから会話の終わりまでを見届ける。
            # 返事が1つも無いまま終わったら、その相手の控えを1つ進める。
            state["watching"] = {"npc_id": str(npc_id), "name": npc_name,
                                 "responded": False, "at": time.monotonic()}

        # 手が空くまで待ってから押す。
        # ここで確立した「移動の後始末（テキストの流し込み・ボタンの張り替え）の最中に割り込むと噛み合わない」は `ui.Screen.when_idle` に移してあり、
        # 会話の終了処理の後始末でも同じものが使われている。
        # 待ちきれなければ諦めるのがこちらの流儀（イベントは出さなくてよいもので、
        # 遅れて出すと場面が変わっている）。
        screen.when_idle(app, start, timeout=IDLE_TIMEOUT, settle=IDLE_SETTLE,
                         poll=IDLE_POLL, cancel_if=cancelled, tag="launch")

    # ================================================================
    # narration モード: 自前で1行だけ生成して情景描写に足す
    # ================================================================
    def build_messages(app, facility, npc):
        player = getattr(app, "player", None)
        area = getattr(player, "current_area", None)
        world = getattr(app, "world", None)
        language = LANGUAGE_NAMES.get(
            str(getattr(app, "language", "")).lower(), "日本語")

        area_overview = ""
        descriptions = getattr(area, "descriptions", None)
        if isinstance(descriptions, dict):
            area_overview = frames.short(descriptions.get("overview"), None)

        recent = ""
        log = getattr(app, "current_narration_log", None)
        if isinstance(log, list) and log:
            last = log[-1]
            if isinstance(last, dict):
                recent = frames.short(last.get("narration"), None)

        instruction = (
            "あなたはダークファンタジーRPGの登場人物を演じます。\n"
            "プレイヤーキャラ'{player_name}'が'{facility_name}'に入ってきました。\n"
            "その場にいる'{npc_name}'が、プレイヤーに短く声をかけます。\n"
            "【出力の決まり】\n"
            "- {language}で、セリフだけを1文か2文。全体で60文字以内。\n"
            "- 鉤括弧「」で括ること。地の文・情景描写・説明・発話者名は書かないこと。\n"
            "- その場の役割（宿の主人なら宿の主人）として自然な、日常的な一言にすること。\n"
            "- 物語を勝手に進めないこと。依頼・事件・重大な報せを持ち出さないこと。\n"
            "- personality と speech_style の記述には強く従うこと。"
        ).format(player_name=ui.player_name(app, fallback="冒険者", limit=60),
                 facility_name=frames.short(getattr(facility, "name", ""), 80),
                 npc_name=frames.short(getattr(npc, "name", ""), 60),
                 language=language)

        # 材料は字数で切らない（窓に収まる根拠は VERIFICATION.md §3.95）。名前と種別の切りは壊れた値への用心。
        context = (
            "【世界観】\n{worldview}\n\n"
            "【現在地】\n- エリア: {area_name}({area_overview})\n"
            "- 施設: {facility_name}({facility_type})\n- 説明: {facility_desc}\n\n"
            "【{npc_name}の情報】\n- プロフィール: {npc_profile}\n"
            "- 人格: {npc_personality}\n- 口調: {npc_speech}\n- 役割: {npc_job}\n"
            "- {player_name}との関係: {relationship}\n\n"
            "【{player_name}の情報】\n- プロフィール: {player_profile}\n\n"
            "【直前の情景】\n{recent}"
        ).format(worldview=frames.short(getattr(world, "worldview", ""), None),
                 area_name=frames.short(getattr(area, "name", ""), 60),
                 area_overview=area_overview,
                 facility_name=frames.short(getattr(facility, "name", ""), 80),
                 facility_type=frames.short(getattr(facility, "facility_type", ""), 40),
                 facility_desc=frames.short(getattr(facility, "description", ""), None),
                 npc_name=frames.short(getattr(npc, "name", ""), 60),
                 npc_profile=frames.short(getattr(npc, "profile", ""), None),
                 npc_personality=frames.short(getattr(npc, "personality", ""), None),
                 npc_speech=frames.short(getattr(npc, "speech_style", ""), None),
                 npc_job=frames.short(getattr(npc, "job", ""), 60),
                 relationship=frames.short(_relationship_of(npc, player), None),
                 player_name=ui.player_name(app, fallback="冒険者", limit=60),
                 player_profile=frames.short(getattr(player, "profile", ""), None),
                 recent=recent)

        return [{"role": "user", "content": instruction + "\n\n" + context},
                {"role": "user", "content": "<状況: プレイヤーが入店・到着した>"}]

    def _relationship_of(npc, player):
        """NPC 側が持っているプレイヤーとの関係。初対面なら空でよい。"""
        rel = getattr(npc, "relationship", None)
        if not isinstance(rel, dict):
            return ""
        name = getattr(player, "name", None)
        for key in ("player", name):
            if key and key in rel:
                return rel[key]
        return ""

    def generate_line(app, facility, npc):
        """セリフを1本作る。作れなければ None（＝情景描写に何も足さない）。

        送信モジュールは名指ししない。
        プロバイダごとに違ううえ、名前を並べた一覧は増えた時点で古くなる（VERIFICATION.md §2.8）。
        `llm.ask` が `llm_manager` の別名から引く（TECH.md §5.3）。
        """
        result = llm.ask(ctx, MANAGER_NAME, build_messages(app, facility, npc),
                         timeout=LINE_TIMEOUT, max_tokens=MAX_TOKENS,
                         label="arrival event", write=write)
        if result is None:
            return None
        line = clean_line(result)
        write("generated: {!r}".format(line))
        return line

    def clean_line(result):
        """戻り値からセリフ1つを取り出す。

        `send_request_with_no_structure` は str を返す（実機で確認済み）。
        それでも型を決め打ちしないのは、モデルが地の文を足してくることがあるため。
        最初の非空行だけを採り、鉤括弧が無ければ付ける。
        """
        if not isinstance(result, str):
            result = "" if result is None else str(result)
        for raw in result.splitlines():
            line = raw.strip()
            if not line:
                continue
            if len(line) > MAX_CHARS:
                line = line[:MAX_CHARS]
            if not line.startswith("「"):
                line = "「" + line.lstrip("「")
            if not line.endswith("」"):
                line = line.rstrip("」") + "」"
            return line
        return None

    def append_to_narration(result, extra):
        """情景描写の戻り値にセリフを足す。形が分からなければ触らない。"""
        if isinstance(result, str):
            return result.rstrip() + "\n" + extra
        if isinstance(result, dict) and isinstance(result.get("text"), str):
            result["text"] = result["text"].rstrip() + "\n" + extra
            return result
        text = getattr(result, "text", None)
        if isinstance(text, str):
            try:
                result.text = text.rstrip() + "\n" + extra
                return result
            except Exception:
                pass
        write("could not append to narration result of type {}".format(
            type(result).__name__))
        return result

    # ------------------------------------------------------------------ フック
    # 施設を移る前後はローダの窓口 `arrivals` から呼ばれる（TECH.md §3.3.21）。
    # 包みは `arrivals.on` が立てる（`arrivals.install` を呼んだのと同じ）。
    def before_move(call):
        if EVENT_MODE == "narration":
            # 情景描写は move_phase の *内側* で呼ばれる。
            # だから印は先に置く。
            state["pending"] = {"at": time.monotonic()}

    def after_move(call):
        state["pending"] = None
        if EVENT_MODE == "conversation":
            try:
                app = find_app()
                decision = decide(app)
                if decision is not None:
                    facility, npc_id, npc = decision
                    launch_conversation(app, facility, npc_id, npc)
            except Exception:
                # イベントの失敗で移動そのものを巻き添えにしない。
                ctx.log_exc("arrival event: launch failed")

    arrivals.on(ARRIVAL_OWNER, ctx, (arrivals.FACILITY,), before=before_move, after=after_move, write=write)

    @ctx.wrap("scripts.llm.llm_manager:narrator", required=False)
    def narrator(orig, *args, **kwargs):
        result = orig(*args, **kwargs)
        if EVENT_MODE != "narration":
            return result
        pending = state["pending"]
        state["pending"] = None
        if pending is None:
            return result
        try:
            if time.monotonic() - pending["at"] > PENDING_TTL:
                return result
            app = find_app()
            decision = decide(app)
            if decision is None:
                return result
            facility, _npc_id, npc = decision
            line = generate_line(app, facility, npc)
        except Exception:
            # イベントの失敗で情景描写まで巻き添えにしない。
            ctx.log_exc("arrival event: generation failed")
            return result
        return append_to_narration(result, line) if line else result

    # ---------------------------------------------- 第一声の読み替え（conversation）
    # ゲーム本来の合図は `<行動: 話しかける>`（＝プレイヤーが話しかけた）。到着イベントは逆なので、
    # こちらが起こした会話のときだけ、ローダの窓口 `arrivals.opening` に第一声を予約して差し替えさせる
    # （TECH.md §3.3.21）。窓口は渡す messages の写しだけを書き換え、ゲームの会話履歴には触らない。
    # 置き換えるのはゲームの合図の1行目だけで、2行目以降は残す（`keep_rest`）。
    # `301_` などがローダの `modnpc` の prompt の口で同じ messages[-1] に改行で足した状況を消さないため。
    # 予約は次の `conversation_starter` で片付き、相手の id が予約と違えば使わない。
    arrivals.install_opening(ctx, write)

    def reserve_opening(app, npc_id, facility_name):
        def text(call):
            return (
                "<状況: {facility}に入ってきた{player}に、あなたの方から声をかけた。"
                "呼び止める第一声を述べよ>"
            ).format(facility=facility_name or "この場所",
                     player=ui.player_name(call.app, fallback="旅人", limit=40))

        def dropped(call):
            # こちらが起こした会話が始まらないまま別の相手との会話が来たら、見届けも捨てる
            # （始まらなかった会話の終わりを待ち続けないため）。
            if call.reason != arrivals.DROP_PARTNER:
                return
            watch = state["watching"]
            if watch is not None and watch.get("npc_id") == call.npc_id:
                state["watching"] = None

        arrivals.opening(app, ARRIVAL_OWNER, npc_id, text, ttl=REPHRASE_TTL, keep_rest=True,
                         dropped=dropped, write=write)

    # ================================================================
    # 応答されずに終わった回数を数える
    # ================================================================
    def watching_now():
        """見届け中の控え。古くなっていたら捨てる。"""
        mark = state["watching"]
        if mark is None:
            return None
        if time.monotonic() - mark["at"] > WATCH_TTL:
            write("watch: {!r} timed out; forgotten".format(mark["name"]))
            state["watching"] = None
            return None
        return mark

    def note_response(npc_id=None):
        """プレイヤーが何か返した。会話が終わるまで覚えておく。"""
        mark = watching_now()
        if mark is None or mark["responded"]:
            return
        # 相手が分かるなら突き合わせる。分からなければ見届け中の相手とみなす。
        # **文字列の id のときだけ**突き合わせること。`app.in_conversation` は
        # 会話中の NPC id が入る（GAME.md）が、真偽値で来る経路に当たった場合に
        # `str(True)` と比べると必ず食い違い、返事を取りこぼす。
        if isinstance(npc_id, str) and npc_id and npc_id != mark["npc_id"]:
            return
        mark["responded"] = True

    def settle_watch(app, partner=None):
        """会話が終わった。返事の有無で控えを進めるか、0 に戻す。

        `partner` は終わった会話の相手。見届けている相手と違えば、その見届けは捨てる
        （声をかけた会話がロードなどで消え、別の人との会話の終わりを無視と数えないため）。
        """
        mark = watching_now()
        if mark is None:
            return
        state["watching"] = None
        npc_id, name = mark["npc_id"], mark["name"]
        if isinstance(partner, str) and partner and partner != npc_id:
            write("watch: the talk that ended was with {!r}, not {!r}; forgotten".format(partner, name))
            return
        store = ignores(app)
        if mark["responded"]:
            if store.pop(npc_id, None) is not None:
                write("watch: {!r} responded; ignore count reset".format(name))
                save_ignores()
            return
        entry = store.get(npc_id)
        count = (entry.get("count", 0) if isinstance(entry, dict) else 0) + 1
        store[npc_id] = {"count": count, "name": name}
        save_ignores()
        if IGNORE_LIMIT and count >= IGNORE_LIMIT:
            write("watch: {!r} ignored {} time(s); will not speak first again"
                  .format(name, count))
        else:
            write("watch: {!r} ignored {} time(s)".format(name, count))

    # 会話の1手と終了処理はローダの窓口 `talks` に預ける（TECH.md §3.3.21）。
    # 相手（`call.partner`）は窓口が orig の前に `app.in_conversation` から読む（終わると下りるため）。
    # 例外は窓口が握って modloader.log と write に残す。
    def turn_before(call):
        """1ターン進んだ＝プレイヤーが返事をした。値には触らない。"""
        note_response(call.partner)

    talks.on(ARRIVAL_OWNER, ctx, (talks.TURN,), before=turn_before, write=write)

    @ctx.wrap("__main__:ConversationEndManager.__init__", required=False)
    def conversation_end_init(orig, self, *args, **kwargs):
        """MOD のボタンで閉じる会話か（`end_text` が画面の「会話を終了する」と違うか）を控える。値には触らない。

        MOD が会話を閉じるときは、画面のボタンの args を写して `end_text` だけ差し替える（GAME.md §2.5）。
        組み立てる時点では、画面にゲーム自身の「会話を終了する」がまだ並んでいる。
        言語で変わる文言を決め打ちしないよう、そのボタンの `end_text` と比べる。
        """
        result = orig(self, *args, **kwargs)
        try:
            # `self` の後ろは app, in_conversation_id, finisher, end_text の順。
            end_text = frames.arg(args, kwargs, "end_text", 3)
            app = frames.arg(args, kwargs, "app", 0) or find_app()
            _partner, entry = ui.conversation_partner(getattr(app, "buttons", None))
            native = ui.spec_args(entry) if entry is not None else None
            if isinstance(end_text, str) and native and len(native) >= 3 and end_text != native[2]:
                setattr(self, MOD_CLOSE_ATTR, True)
        except Exception:
            pass
        return result

    def finish_before(call):
        # 「会話を終了する」以外の閉じ方は、MOD のボタン（依頼の話・別れ話など）を押した＝返事をした。
        if getattr(call.obj, MOD_CLOSE_ATTR, False):
            note_response(call.partner)

    def finish_after(call):
        """会話の終わり。ここで数える（orig が返った回だけ）。"""
        settle_watch(call.app, call.partner)

    talks.on(ARRIVAL_OWNER, ctx, (talks.FINISH,), before=finish_before, after=finish_after, write=write)

    ctx.log("arrival events: mode={} chance={} log={}".format(
        EVENT_MODE,
        "override {}".format(CHANCE_OVERRIDE) if CHANCE_OVERRIDE is not None
        else "per-type table", log_path))
