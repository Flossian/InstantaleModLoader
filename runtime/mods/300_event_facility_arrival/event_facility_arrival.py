# -*- coding: utf-8 -*-
"""機能追加: 施設に着くと、その場の NPC の方から会話を始めてくる。

宿屋に入れば主人が、店に入れば店主が声をかけてくる ―
「プレイヤーの行動をトリガーにしたイベント」。

## 2つのモード

    EVENT_MODE = "conversation"   ゲーム本来の会話フェーズを開始する（既定）
    EVENT_MODE = "narration"      情景描写に NPC のセリフを1行足すだけ

**conversation** は、プレイヤーが「会話する」→ NPC を選んだときと**同じ経路**を
こちらから起こす。したがって立ち絵の表示・会話履歴・関係値の更新・会話の終了処理は
全てゲーム本来の実装がそのまま動く。実プレイの計測で確かめた本来の経路:

```
process_choice(DisplayTalkChoice,        choice_text='会話する')
process_choice(ConversationStartManager, choice_text='マナ')      ← NPC を選んだ瞬間
process_choice(ConversationEndManager,   choice_text='会話を終了する')
```

つまり `app.process_choice(ConversationStartManager(app, npc_id), npc_name)` が
「その NPC のボタンを押した」に相当する。`DisplayTalkChoice`（NPC 一覧の表示）は
挟まない ― こちらが相手を決めているため。

**narration** は初版の挙動で、自前で LLM を1回呼んで情景描写に1行足す。立ち絵も
会話モードも無いが LLM 1回で済む。conversation が合わない場面用に残してある。

## いつ発火させるか

`__main__:MovePhaseManager.move_phase` の**復帰後**。計測で分かったこの関数の構造:

```
process_choice(MovePhaseManager, ...)    ボタン押下
  move_phase()
    narrator(...)                        ← 情景描写は move_phase の *内側* で呼ばれる
  move_phase 復帰                        ← ここで到着が確定している
```

初版は「move_phase の復帰後に印を置き、次の narrator で回収する」形にしていたが、
narrator が内側にある以上それは**1手ずれる**（印を回収するのは次の移動の narrator）。
narration モードでは印を `orig` の**前**に置いて、入れ子の narrator に回収させる。

会話フェーズの開始は、移動の後始末（テキストの流し込み・ボタンの張り替え）が
終わってからでないと噛み合わない。そこで Kivy の Clock で
`is_adding_text` / `is_button_enabled` を見張り、**手が空いた時点で**押す。
これは「テキストが出終わってからプレイヤーがボタンを押す」のと同じ状況になる。

## その時点で読める情報（実機のスナップショットで確認済み）

    app.player.location      -> Facility   name / description / facility_type /
                                           owner / characters(その場の NPC id)
    app.world.characters     -> {id: Character}    Facility.owner はこの id

## 発生率

`CHANCE_OVERRIDE` が None でなければその確率を全施設に使う（動作確認用）。
None なら施設種別ごとの `CHANCE_BY_TYPE`。乱数はこの mod 専用の
`random.Random` を使う ― グローバルから引くとゲーム自身の乱数列がずれるため
（104_balance_area_bgm.py と同じ方針）。同じ施設で連続しては出さない。
"""

import datetime
import random
import sys
import time

from instantale_modloader import ui

NAME = "NPC greets on arrival"
NAME_JA = "施設でNPCが話しかける"
VERSION = "1"
DESCRIPTION = "An NPC at the facility starts a conversation when you arrive"
DESCRIPTION_JA = "施設に着くと、その場に居る NPC の方から会話を始めてくるようにする"
AUTHOR = "R01/Flossian"

LOG_BASENAME = "player_events.log"

# "conversation"（立ち絵つきの会話フェーズ） / "narration"（1行だけ足す）
EVENT_MODE = "conversation"

# 施設種別ごとの発生率。ここに無い種別では発生しない。
# 'ward' / 'location' / 'entrance' / 'exit' は「主のいない通路」なので入れていない。
CHANCE_BY_TYPE = {
    "inn": 0.50,
    "guild": 0.30,
    "general_store": 0.25,
    "specialty_shop": 0.25,
    "blacksmith": 0.15,
    "medical_facility": 0.20,
    "administrative_office": 0.25,
    "underworld_office": 0.20,
}

# None なら上の表を使う。数値ならその確率を全施設種別に適用する（動作確認用）。
# **確認が済んだら None に戻すこと。**
#CHANCE_OVERRIDE = 1.0
CHANCE_OVERRIDE = None

# 同じ施設で連続して発火させない。直近この回数の移動のうちに出した施設は飛ばす。
COOLDOWN_MOVES = 3

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

# ここが真の間はイベントを出さない ― 戦闘中・会話中など。
# 「手が空いているか」（テキストの流し込み中・操作を受け付けていない・
# ポップアップが開いている）の判定は `ui.IDLE_SIGNALS` 側にあり、そちらは
# `301_` / `302_` も使う。ここは**イベントを出す前提が崩れている状態**の一覧。
BUSY_FLAGS = ("in_battle", "in_boss_battle", "in_colosseum_battle",
              "in_conversation", "in_free_input",
              "in_action_in_conversation")

# `in_shopping` はここに**入れない**（2026-07-26 の実測による）。
# 店の外をただ往復しているだけの 38 回の移動すべてで True のままだった
# （`events.log` の 01:13〜01:16。全て MovePhaseManager による素の移動）。
# 「買い物中か」の信号としては当てにならず、これを見ていると店系の施設で
# イベントがほとんど出なくなる ― 実際、この間の不発 39 件のうち 38 件が
# これだった。買い物窓が開いている状態は `is_popup_window_opened`
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
        "fired_at": {},       # 施設 id -> 発火したときの move_no
        "rephrase": None,     # conversation モード: 第一声の読み替え待ち
        "npc_id_kind": None,  # ゲーム自身が character_id に何を渡しているか
    }

    def write(text):
        try:
            with open(log_path, "a", encoding="utf-8") as fh:
                fh.write("[{}] {}\n".format(
                    datetime.datetime.now().isoformat(timespec="milliseconds"), text))
        except Exception:
            ctx.log_exc("arrival event: write failed")

    # 選択肢まわりと「手が空くのを待つ」は `instantale_modloader.ui` に集約
    # してある（`301_` / `302_` と共有）。この mod で確立した
    # 「移動の後始末が終わってから押す」も、会話の終了処理の後始末に同じ形で
    # 効くので、そちらから使えるようにそこへ移した。
    screen = ui.Screen(ctx, write, tag="arrival event")
    find_app = ui.find_app

    # ------------------------------------------------------------ 発火の判定
    def chance_for(facility_type):
        if CHANCE_OVERRIDE is not None:
            # 表に無い種別（通路など）は override でも対象外のままにする。
            return CHANCE_OVERRIDE if facility_type in CHANCE_BY_TYPE else 0.0
        return CHANCE_BY_TYPE.get(facility_type, 0.0)

    def pick_speaker(app, facility):
        """話しかけてくる NPC を (id, インスタンス) で返す。主がいれば主。"""
        characters = getattr(getattr(app, "world", None), "characters", None)
        if not isinstance(characters, dict):
            return None, None
        owner = getattr(facility, "owner", None)
        if owner is not None:
            npc_id = str(owner)
            npc = characters.get(npc_id)
            if npc is not None:
                return npc_id, npc
        # 施設に居合わせた NPC（重複することがあるので一意化してから選ぶ）。
        present = getattr(facility, "characters", None)
        if isinstance(present, (list, tuple)) and present:
            ids = sorted({str(cid) for cid in present if str(cid) in characters})
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

        last = state["fired_at"].get(facility_id)
        if last is not None and state["move_count"] - last <= COOLDOWN_MOVES:
            write("skip: {} on cooldown".format(where))
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
        state["fired_at"][facility_id] = state["move_count"]
        return facility, npc_id, npc

    # ================================================================
    # conversation モード: ゲーム本来の会話フェーズを開始する
    # ================================================================
    def launch_conversation(app, facility, npc_id, npc):
        """「その NPC のボタンを押した」のと同じことをする。

        押すのは *今すぐ* ではない。移動の後始末（テキストの流し込み・ボタンの
        張り替え）の最中に割り込むと噛み合わないので、手が空くまで待つ。
        待ち合わせは Kivy の Clock で行う ― ゲーム自身が UI を触るのと同じ土俵。
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
        module = sys.modules.get(
            "scripts.llm.request_llm_inference_llama_cpp_completion")
        send = getattr(module, "send_request_with_no_structure", None) if module else None
        if send is None:
            write("skip: send_request_with_no_structure unavailable")
            return None
        messages = build_messages(app, facility, npc)
        started = time.monotonic()
        result = send(MANAGER_NAME, messages, max_tokens=MAX_TOKENS)
        line = clean_line(result)
        write("generated in {:.1f}s: {!r}".format(time.monotonic() - started, line))
        return line

    def clean_line(result):
        """戻り値からセリフ1つを取り出す。

        `send_request_with_no_structure` は str を返す（実機で確認済み）。
        それでも型を決め打ちしないのは、モデルが地の文を足してくることが
        あるため ― 最初の非空行だけを採り、鉤括弧が無ければ付ける。
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

        threading.Thread(target=_run, name="instantale_mod.arrival_selftest",
                         daemon=True).start()

    ctx.log("arrival events: mode={} chance={} log={}".format(
        EVENT_MODE,
        "override {}".format(CHANCE_OVERRIDE) if CHANCE_OVERRIDE is not None
        else "per-type table", log_path))
