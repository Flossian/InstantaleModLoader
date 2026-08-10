# -*- coding: utf-8 -*-
"""機能追加: 施設に着くと、その場の NPC の方から会話を始めてくる。

宿屋に入れば主人が、店に入れば店主が声をかけてくる。
「プレイヤーの行動をトリガーにしたイベント」。

## 2つのモード

    EVENT_MODE = "conversation"   ゲーム本来の会話フェーズを開始する（既定）
    EVENT_MODE = "narration"      情景描写に NPC のセリフを1行足すだけ

**conversation** は、プレイヤーが「会話する」→ NPC を選んだときと**同じ経路**を
こちらから起こす。したがって立ち絵の表示・会話履歴・関係値の更新・会話の終了処理は
全てゲーム本来の実装がそのまま動く。ゲーム本来の経路（GAME.md §2.5）:

```
process_choice(DisplayTalkChoice,        choice_text='会話する')
process_choice(ConversationStartManager, choice_text='マナ')      ← NPC を選んだ瞬間
process_choice(ConversationEndManager,   choice_text='会話を終了する')
```

つまり `app.process_choice(ConversationStartManager(app, npc_id), npc_name)` が
「その NPC のボタンを押した」に相当する。`DisplayTalkChoice`（NPC 一覧の表示）は
挟まない。こちらが相手を決めているため。

**narration** は自前で LLM を1回呼んで情景描写に1行足すだけの軽い方。立ち絵も
会話モードも無いが LLM 1回で済む。conversation が合わない場面用に用意してある。

## いつ発火させるか

`__main__:MovePhaseManager.move_phase` の**復帰後**。この関数の構造（GAME.md §2.6）:

```
process_choice(MovePhaseManager, ...)    ボタン押下
  move_phase()
    narrator(...)                        ← 情景描写は move_phase の *内側* で呼ばれる
  move_phase 復帰                        ← ここで到着が確定している
```

narrator が **`move_phase` の内側**にあるので、印を復帰後に置くと**1手ずれる**
（回収するのは次の移動の narrator になる）。narration モードでは印を `orig` の
**前**に置いて、入れ子の narrator に回収させる。

会話フェーズの開始は、移動の後始末（テキストの流し込み・ボタンの張り替え）が
終わってからでないと噛み合わない。そこで Kivy の Clock で
`is_adding_text` / `is_button_enabled` を見張り、**手が空いた時点で**押す。
これは「テキストが出終わってからプレイヤーがボタンを押す」のと同じ状況になる。

## その時点で読める情報（GAME.md §2.7）

    app.player.location      -> Facility   name / description / facility_type /
                                           owner / characters(その場の NPC id)
    app.world.characters     -> {id: Character}    Facility.owner はこの id

## 発生率

施設種別ごとに `CHANCE_INN` / `CHANCE_GUILD` / … があり、**0 にすればその種別では
出なくなる**。`CHANCE_OVERRIDE` が None でなければそちらを全施設に使う（動作確認用）。
どれも `mod.json` から変えられる。乱数はこの mod 専用の
`random.Random` を使う。グローバルから引くとゲーム自身の乱数列がずれるため
（104_balance_area_bgm.py と同じ方針）。

**この % は「その施設に入った1回あたり」**の値。同じ施設で続けて出ないように、
発火した施設は `COOLDOWN_VISITS` 回ぶん訪問を挟むまで抽選しない。
"""

import random
import sys
import time

from instantale_modloader import llm, ui

LOG_BASENAME = "player_events.log"

# "conversation"（立ち絵つきの会話フェーズ） / "narration"（1行だけ足す）
EVENT_MODE = "conversation"

# 施設種別ごとの発生率。**0 にするとその種別では出なくなる。**
# ここに項目が無い種別（'ward' / 'location' / 'entrance' / 'exit' ＝ 主のいない
# 通路）では発生しない。
#
# 表（dict）ではなく **1種別1定数** にしてあるのは、`mod.json` の "settings" で
# 宣言できる型が bool/int/float/str/choice だけだから（config.py）。辞書のままだと
# GUI から触れず、「プレイヤーの体験に関わる設定は mod.json に置く」方針から外れる。
# 施設種別はセーブの `facility_type` そのもので、ゲーム側で閉じた集合なので
# 1つずつ並べても増えない。
CHANCE_INN = 0.50
CHANCE_GUILD = 0.30
CHANCE_GENERAL_STORE = 0.25
CHANCE_SPECIALTY_SHOP = 0.25
CHANCE_BLACKSMITH = 0.15
CHANCE_MEDICAL_FACILITY = 0.20
CHANCE_ADMINISTRATIVE_OFFICE = 0.25
CHANCE_UNDERWORLD_OFFICE = 0.20

# None なら上の表を使う。数値ならその確率を全施設種別に適用する（動作確認用）。
# **確認が済んだら None に戻すこと。**
#CHANCE_OVERRIDE = 1.0
CHANCE_OVERRIDE = None

# 同じ施設で続けて出さない間隔。**その施設に入った回数**で数える。
# 2 なら「出た後、その施設に2回入るまでは出さない」（＝ 3回に1回まで）。
#
# 元は移動回数で数えていたが（`COOLDOWN_MOVES = 3`）、それだと出入りするだけで
# 抜けてしまい、**同じ施設で繰り返し出る**という体感になっていた。実際、同じ宿の
# 同じ NPC が1日に3回発火していた（ログの `roll 0.23 / 0.02 / 0.17`）。
# 数える単位を「その施設への訪問」に変えると、間隔がそのまま訪問回数で決まる。
#
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

# セリフ1本にかける上限（秒）。**必ず渡す。** ゲーム側の既定は無期限で、
# ここは narrator の中＝情景描写のスレッドなので、返らないと画面ごと止まる。
LINE_TIMEOUT = 30.0

# ここが真の間はイベントを出さない ― 戦闘中・会話中など。
# 「手が空いているか」（テキストの流し込み中・操作を受け付けていない・
# ポップアップが開いている）の判定は `ui.IDLE_SIGNALS` 側にあり、そちらは
# `301_` / `302_` も使う。ここは**イベントを出す前提が崩れている状態**の一覧。
BUSY_FLAGS = ("in_battle", "in_boss_battle", "in_colosseum_battle",
              "in_conversation", "in_free_input",
              "in_action_in_conversation")

# `in_shopping` はここに**入れない**。店の外をただ往復しているだけの移動でも
# True のままなので、「買い物中か」の信号としては当てにならない ― これを見ていると
# 店系の施設でイベントがほとんど出なくなる。買い物窓が開いている状態は
# `is_popup_window_opened`
# （会話を始める直前に見る）で弾ける。

LANGUAGE_NAMES = {"japanese": "日本語", "english": "英語"}

# 注入した瞬間に、今いる施設で narration モードのセリフを1本作ってログにだけ出す。
# 画面には出さないし状態も変えない（会話フェーズは開始しない）。
SELFTEST_ON_BOOT = False


def _text_of(value, limit=400):
    """LLM に渡す値を安全に文字列化する。None や非文字列でも落ちないように。"""
    if value is None:
        return ""
    if not isinstance(value, str):
        value = str(value)
    value = value.strip()
    return value if len(value) <= limit else value[:limit] + "…"


def apply(ctx):
    log_path = ctx.out_path(LOG_BASENAME)
    # この mod 専用の乱数源。ゲーム自身の乱数列に影響を与えないため。
    rng = random.Random()
    state = {
        "pending": None,      # narration モード: 入れ子の narrator が回収する印
        "move_count": 0,
        "visits": {},         # 施設 id -> その施設に入った回数
        "fired_at": {},       # 施設 id -> 発火したときの訪問回数
        "rephrase": None,     # conversation モード: 第一声の読み替え待ち
        "npc_id_kind": None,  # ゲーム自身が character_id に何を渡しているか
    }

    write = ctx.logger(LOG_BASENAME)

    # 選択肢まわりと「手が空くのを待つ」は `instantale_modloader.ui` に集約
    # してある（`301_` / `302_` と共有）。この mod で確立した
    # 「移動の後始末が終わってから押す」も、会話の終了処理の後始末に同じ形で
    # 効くので、そちらから使えるようにそこへ移した。
    screen = ui.Screen(ctx, write, tag="arrival event")
    find_app = ui.find_app

    # 施設種別ごとの発生率を表に組み直す。**ここ（apply の中）で組むこと** ―
    # 設定の反映はモジュールのグローバルへの書き込みで、それが済むのは
    # apply() を呼ぶ直前だから（config.apply_to_module）。モジュールの
    # トップレベルで組むと、利用者が選んだ値ではなく既定値の表になる。
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
    def chance_for(facility_type):
        if CHANCE_OVERRIDE is not None:
            # 表に無い種別（通路など）は override でも対象外のままにする。
            return CHANCE_OVERRIDE if facility_type in chance_table else 0.0
        return chance_table.get(facility_type, 0.0)

    def is_at(character, facility):
        """NPC の現在地が対象施設なら True を返す。

        現在地を読めない相手は施設の名簿を信じて True に倒す。名簿にしか
        居場所が無い NPC を弾くと、話者が一人も選ばれずイベント自体が
        出なくなる。
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

    def pick_speaker(app, facility):
        """施設に現在居る話者を (id, インスタンス) で返す。主がいれば主。"""
        characters = getattr(getattr(app, "world", None), "characters", None)
        if not isinstance(characters, dict):
            return None, None
        owner = getattr(facility, "owner", None)
        if owner is not None:
            npc_id = str(owner)
            npc = characters.get(npc_id)
            if npc is not None and is_at(npc, facility):
                return npc_id, npc
        # 施設に居合わせた NPC（重複することがあるので一意化してから選ぶ）。
        present = getattr(facility, "characters", None)
        if isinstance(present, (list, tuple)) and present:
            ids = sorted({
                str(cid) for cid in present
                if str(cid) in characters and is_at(characters[str(cid)], facility)
            })
            if ids:
                npc_id = rng.choice(ids)
                return npc_id, characters[npc_id]
        return None, None

    def decide(app):
        """発火条件を全て満たすなら (施設, NPC id, NPC) を返す。でなければ None。"""
        if app is None:
            return None

        # 「そもそも出しうる場所か」を先に見る。通路（対象外）での不発まで
        # 記録するとログが読めなくなるので、ここまでは黙って落とす。
        facility = getattr(getattr(app, "player", None), "location", None)
        if facility is None:
            return None
        facility_id = str(getattr(facility, "id", ""))
        facility_type = getattr(facility, "facility_type", None)
        chance = chance_for(facility_type)
        if chance <= 0.0:
            return None
        where = "{} ({})".format(getattr(facility, "name", ""), facility_type)

        # ここまで来た＝この施設に入った。間引きはこの回数で数える。
        visits = state["visits"].get(facility_id, 0) + 1
        state["visits"][facility_id] = visits

        last = state["fired_at"].get(facility_id)
        if last is not None and visits - last <= COOLDOWN_VISITS:
            write("skip: {} on cooldown ({}/{} visit(s) since the last one)".format(
                where, visits - last, COOLDOWN_VISITS))
            return None

        # 状態の判定は施設が決まってから。どこで何に邪魔されたのかが分かる。
        busy = [flag for flag in BUSY_FLAGS if getattr(app, flag, False)]
        if busy:
            write("skip: {} busy {}".format(where, busy))
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
        return facility, npc_id, npc

    # ================================================================
    # conversation モード: ゲーム本来の会話フェーズを開始する
    # ================================================================
    def launch_conversation(app, facility, npc_id, npc):
        """「その NPC のボタンを押した」のと同じことをする。

        押すのは *今すぐ* ではない。移動の後始末（テキストの流し込み・ボタンの
        張り替え）の最中に割り込むと噛み合わないので、手が空くまで待つ。
        待ち合わせは Kivy の Clock で行う。ゲーム自身が UI を触るのと同じ土俵。
        """
        main = sys.modules.get("__main__")
        manager_cls = getattr(main, "ConversationStartManager", None) if main else None
        if manager_cls is None:
            write("launch: ConversationStartManager not found")
            return
        npc_name = getattr(npc, "name", None)
        if not npc_name:
            write("launch: speaker has no name")
            return

        def gone():
            """待ち合わせを取り消す条件。理由を返す（無ければ None）。"""
            if getattr(getattr(app, "player", None), "location", None) is not facility:
                return "player left {!r}".format(getattr(facility, "name", ""))
            busy = [flag for flag in BUSY_FLAGS if getattr(app, flag, False)]
            if busy:
                return "became busy {}".format(busy)
            return None

        def start():
            # 待っている間に施設を出ていたら、もう始めない。
            reason = gone()
            if reason:
                write("launch: {}; cancelled".format(reason))
                return
            if REPHRASE_OPENING:
                # 第一声を「NPC の方から声をかけた」に読み替える印。
                state["rephrase"] = {"at": time.monotonic(), "npc": npc_name,
                                     "facility": getattr(facility, "name", "")}
            try:
                manager = manager_cls(app, npc_id)
            except Exception:
                ctx.log_exc("launch: cannot build ConversationStartManager")
                state["rephrase"] = None
                return
            write("launch: process_choice(ConversationStartManager, {!r}) npc_id={!r}"
                  .format(npc_name, npc_id))
            try:
                app.process_choice(manager, npc_name)
            except Exception:
                ctx.log_exc("launch: process_choice failed")
                state["rephrase"] = None

        # 手が空くまで待ってから押す。ここで確立した「移動の後始末
        # （テキストの流し込み・ボタンの張り替え）の最中に割り込むと噛み合わない」
        # は `ui.Screen.when_idle` に移してあり、会話の終了処理の後始末でも
        # 同じものが使われている。**待ちきれなければ諦める**のがこちらの流儀
        # （イベントは出さなくてよいもので、遅れて出すと場面が変わっている）。
        screen.when_idle(app, start, timeout=IDLE_TIMEOUT, settle=IDLE_SETTLE,
                         poll=IDLE_POLL, cancel_if=gone, tag="launch")

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
            area_overview = _text_of(descriptions.get("overview"), 600)

        recent = ""
        log = getattr(app, "current_narration_log", None)
        if isinstance(log, list) and log:
            last = log[-1]
            if isinstance(last, dict):
                recent = _text_of(last.get("narration"), 300)

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
        ).format(player_name=_text_of(getattr(player, "name", "冒険者"), 60),
                 facility_name=_text_of(getattr(facility, "name", ""), 80),
                 npc_name=_text_of(getattr(npc, "name", ""), 60),
                 language=language)

        context = (
            "【世界観】\n{worldview}\n\n"
            "【現在地】\n- エリア: {area_name}({area_overview})\n"
            "- 施設: {facility_name}({facility_type})\n- 説明: {facility_desc}\n\n"
            "【{npc_name}の情報】\n- プロフィール: {npc_profile}\n"
            "- 人格: {npc_personality}\n- 口調: {npc_speech}\n- 役割: {npc_job}\n"
            "- {player_name}との関係: {relationship}\n\n"
            "【{player_name}の情報】\n- プロフィール: {player_profile}\n\n"
            "【直前の情景】\n{recent}"
        ).format(worldview=_text_of(getattr(world, "worldview", ""), 600),
                 area_name=_text_of(getattr(area, "name", ""), 60),
                 area_overview=area_overview,
                 facility_name=_text_of(getattr(facility, "name", ""), 80),
                 facility_type=_text_of(getattr(facility, "facility_type", ""), 40),
                 facility_desc=_text_of(getattr(facility, "description", ""), 400),
                 npc_name=_text_of(getattr(npc, "name", ""), 60),
                 npc_profile=_text_of(getattr(npc, "profile", ""), 400),
                 npc_personality=_text_of(getattr(npc, "personality", ""), 300),
                 npc_speech=_text_of(getattr(npc, "speech_style", ""), 200),
                 npc_job=_text_of(getattr(npc, "job", ""), 60),
                 relationship=_text_of(_relationship_of(npc, player), 200),
                 player_name=_text_of(getattr(player, "name", "冒険者"), 60),
                 player_profile=_text_of(getattr(player, "profile", ""), 400),
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

        送信モジュールは名指ししない ― プロバイダごとに違ううえ、名前を並べた
        一覧は増えた時点で古くなる（`llama_cpp` と `any_server` しか知らないまま、
        Gemini / OpenAI / Claude では毎回空振りしていた）。`llm.ask` が
        `llm_manager` の別名から引く（TECH.md §5.3）。
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
        それでも型を決め打ちしないのは、モデルが地の文を足してくることが
        あるため。最初の非空行だけを採り、鉤括弧が無ければ付ける。
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
    @ctx.wrap("__main__:MovePhaseManager.move_phase", required=False)
    def move_phase(orig, self, *args, **kwargs):
        state["move_count"] += 1
        if EVENT_MODE == "narration":
            # 情景描写は move_phase の *内側* で呼ばれる。だから印は先に置く。
            state["pending"] = {"at": time.monotonic(), "move_no": state["move_count"]}
        result = orig(self, *args, **kwargs)
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
        return result

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
    @ctx.wrap("scripts.llm.llm_manager:conversation_starter", required=False)
    def conversation_starter(orig, messages, *args, **kwargs):
        """こちらが起こした会話のときだけ、開始の合図を差し替える。

        ゲーム本来の合図は `<行動: 話しかける>`（＝プレイヤーが話しかけた）。
        到着イベントは逆なので、**渡す messages のコピーだけ**を書き換える。
        ゲームが保持している会話履歴そのものには触らない。
        """
        mark = state["rephrase"]
        if mark is None or not REPHRASE_OPENING:
            return orig(messages, *args, **kwargs)
        state["rephrase"] = None
        if time.monotonic() - mark["at"] > REPHRASE_TTL:
            write("rephrase: stale marker, left untouched")
            return orig(messages, *args, **kwargs)
        if not isinstance(messages, list) or not messages:
            return orig(messages, *args, **kwargs)
        last = messages[-1]
        if not isinstance(last, dict) or "content" not in last:
            return orig(messages, *args, **kwargs)
        replacement = dict(last)
        replacement["content"] = (
            "<状況: {facility}に入ってきた{player}に、あなたの方から声をかけた。"
            "呼び止める第一声を述べよ>"
        ).format(facility=mark["facility"] or "この場所",
                 player=_text_of(getattr(getattr(find_app(), "player", None),
                                         "name", "旅人"), 40))
        write("rephrase: {!r} -> {!r}".format(
            _text_of(last.get("content"), 60), replacement["content"]))
        return orig(messages[:-1] + [replacement], *args, **kwargs)

    # ------------------------------- ゲーム自身が使う character_id の形を控える
    @ctx.wrap("__main__:ConversationStartManager.__init__", required=False)
    def conversation_start_init(orig, self, app, character_id, *args, **kwargs):
        kind = "{} {!r}".format(type(character_id).__name__, character_id)
        if state["npc_id_kind"] != kind:
            state["npc_id_kind"] = kind
            write("observed ConversationStartManager(character_id={})".format(kind))
        return orig(self, app, character_id, *args, **kwargs)

    # ------------------------------------------------------------ 自己テスト
    def selftest():
        app = find_app()
        facility = getattr(getattr(app, "player", None), "location", None)
        if facility is None:
            write("selftest: no current facility (not in a game?)")
            return
        npc_id, npc = pick_speaker(app, facility)
        write("selftest: facility={!r} type={!r} speaker={!r} id={!r}".format(
            getattr(facility, "name", ""), getattr(facility, "facility_type", ""),
            getattr(npc, "name", None), npc_id))
        if npc is not None:
            write("selftest result: {!r}".format(generate_line(app, facility, npc)))

    if SELFTEST_ON_BOOT:
        import threading

        def _run():
            try:
                selftest()
            except Exception:
                ctx.log_exc("arrival event: selftest failed")

        # **`on_ready` に預ける**（TECH.md §3.6）。`apply()` は再注入と遅延
        # 当て直しで最大8回走るので、ここで直に起こすと**そのたびに LLM を
        # 1回呼ぶ**。1回きりの副作用は印を付けて1回に畳む。
        ctx.on_ready(lambda: threading.Thread(
            target=_run, name="instantale_mod.arrival_selftest",
            daemon=True).start(), key="300_event_facility_arrival:selftest")

    ctx.log("arrival events: mode={} chance={} log={}".format(
        EVENT_MODE,
        "override {}".format(CHANCE_OVERRIDE) if CHANCE_OVERRIDE is not None
        else "per-type table", log_path))
