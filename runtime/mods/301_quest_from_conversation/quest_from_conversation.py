# -*- coding: utf-8 -*-
"""機能追加: NPC との会話から依頼を受注する。

会話中の「行動」メニューに **「依頼を受ける」** を足す。押すと個別の依頼が
一覧で並び、選ぶとゲーム本来の受注画面（`QuestChoiceManager`）に入る。
一覧の先頭には **「この話から依頼を作る」** があり、いま交わしている会話の
内容から依頼を1件その場で生成する。

## ボタンの仕組み（206_ の計測で確定）

    app.buttons = [{'text': '会話する', 'spec': PhaseSpec('DisplayTalkChoice', [])},
                   {'text': 'テストNPC B', 'spec': PhaseSpec('ConversationStartManager', ['73'])},
                   {'text': '出る',      'spec': PhaseSpec('MovePhaseManager', ['20','134','7'])}]
    app.to_display_buttons   表示中の文字列
    app.display_button_map   表示位置 -> buttons の添字
    app.refresh_choice_buttons(reset_page=True)   並べ直す

`PhaseSpec(cls_name, args)` は**マネージャのインスタンスではなくその作り方**を
持つ。押されると `getattr(__main__, cls_name)(app, *args)` が組み立てられ、
`app.process_choice(それ, ボタン文字列)` に渡る。したがって依頼の一覧は

    {'text': '【45】瘴霧の夜警依頼',
     'spec': PhaseSpec('QuestChoiceManager', ['normal_quest', '18'])}

と書くだけでよい。受注画面も承諾処理もゲーム本来の実装がそのまま動く。

## 自前のクラスを cls_name にしない

`PhaseSpec.to_dict()` が存在する ＝ **ボタンはセーブに焼き込まれうる**。
`cls_name='ModQuestOfferManager'` のような自前のクラス名を書くと、mod を
入れずに次回起動したときに `getattr(__main__, ...)` が失敗する。注入は
プロセスと一緒に消える（TECH.md §2）以上、これは必ず起きる状況。

そこで**自前のボタンには無害な既存クラス（`JustSetButtonToNormalPhase`）を
持たせ**、押下は `InstantaleApp.on_button_press` を包んで**文字列ではなく
ボタン辞書に付けた印 `mod_action` で**横取りする。mod が無ければ、残骸の
ボタンは「選択肢を元に戻す」だけの無害な動作になる。

## 会話内容からの生成

ゲーム自身の `DisplayQuestChoice.generate_random_quest()` をそのまま呼ぶ。
これは「いまこの土地に依頼を1件作って登録する」入口で、クエストエリアの生成・
id の採番・`world_dict['quests']` への登録・セーブまで面倒を見てくれる
（自前で `index['quest']` を進めたりエリアを作ったりする必要が無い）。

その内側で走る `llm_manager_world_generate:random_quest_generator` を包み、
`area_description` に**この会話の書き起こしを添える**。引数の書き換えだけなので
出力スキーマ（QuestStructure）はゲームのものが 1 バイトも変わらない。
GAME.md §3「ゲーム自身のヘルパを探す」と同じ手口。

印は1回で使い切る。掲示板から普通に生成した依頼は素通しする。

## 依頼人の人物像（`311_npc_profile_memory` を入れている場合）

同じ相手と何度も話していると、あちらが `state/npc_profiles/<世界名>.json` に
人物像を貯めている。それを**読むだけ**添えて、`client_statement` の口ぶりを
その人物に寄せる。依頼の中身は今の会話だけから決めさせる（人物像を中身に
使わせると、話していない用件を持ち出す）。

**mod を import はしない。** ローダは mod を `instantale_mod_<フォルダ名>` で
登録するので、番号を振り直した瞬間に名前で掴む側が壊れる（TECH.md §3.2.3）。
繋がるのは同じファイルを読むことによってで、ファイルが無ければ何も添えない。
"""

import os
import sys
import time

from instantale_modloader import ui
from instantale_modloader.state import world_filename, world_key
from instantale_modloader.frames import repr_value

LOG_BASENAME = "quest_offer.log"

# 「どの依頼がどの NPC 発か」の控え。**セーブには書かない。**置き場は
# `state/`（`ctx.state_path`）― 消すと受注済みの依頼の出所が分からなくなる、
# 遊びの続きに要るデータなので、ログと同じ場所には置かない。
# クエスト辞書に独自キーを足すとセーブに焼かれるうえ、再読み込み後に
# `Quest` インスタンスがそのキーを持つ保証が無い（`Quest.__init__` が何を
# 写すかは読めない）。mod 側に持てば、ゲームのデータを一切汚さずに済む。
CLIENTS_BASENAME = "quest_clients.json"

# 会話から開いた掲示板を、その NPC 発の依頼だけに絞る。
FILTER_BY_NPC = True

# ボタンの文字列。
OFFER_LABEL = "依頼を受ける"
# 会話中はこちら。押すと会話が終わるので、それが分かる文言にしておく
# （黙って会話を切ると、プレイヤーからは不具合に見える）。
CONVERSATION_OFFER_LABEL = "依頼を受ける（話を切り上げる）"
GENERATE_LABEL = "この話から依頼を作る"
CANCEL_LABEL = "やめる"

# セーブから復元された残骸を見分けるための、こちらのラベル一覧。
# 生成ボタンは「この話から依頼を作る（NPC名）」と後ろが変わるので**前方一致**で
# 照合する（`ui.Screen.prune_stale`）。
OUR_LABELS = (OFFER_LABEL, CONVERSATION_OFFER_LABEL, GENERATE_LABEL)

# 会話を終わらせてから掲示板を開くまでの待ち。会話の終了処理は要約のために
# LLM を回すことがあるので、状態が落ちるのを見張る。
END_POLL = 0.3
END_TIMEOUT = 120.0

# 会話を閉じるときにゲームへ渡す end_text（`302_` の手口を反映）。
# ここは `'<行動: 会話を終了する>'` という自由記述なので、事情を書いておけば
# **会話の要約とライフログに「依頼の話のために切り上げた」がそのまま残る**。
# 引数の意味を推測せずに済み、記録も正しくなる。
END_TEXT = "<行動: 依頼の話を切り出すため、会話を切り上げた>"

# 「依頼を受ける」をどこに出すか。両方入れておいてよい（重複しないよう
# 相互に確認する）。
#
#   "conversation" NPC と話している画面。「会話を終了する」の手前に出す。
#                  会話中の app.buttons は ['会話を終了する'] の1個だけだと
#                  実測できたので、「行動」メニューを経由する必要は無い
#   "facility"     施設の選択肢に「会話する」と並べて出す
#   "action_menu"  会話中の「行動」メニュー（`toggle_to_action_in_conversation`
#                  ＝ 会話画面の上部右ボタン）。そこは HUD 上部のボタンで
#                  app.buttons とは別系統らしく、一度も発火していない
#
# **会話中だけに出す。** この機能の値打ちは「話の流れから依頼になる」ことで、
# 施設に出すとゲーム本来の「クエスト掲示板」（`DisplayQuestChoice`）と同じ動作の
# 重複ボタンになるだけ。
OFFER_SITES = ("conversation",)

# 施設側から入ったときも「この話から依頼を作る」を使えるようにするため、
# 会話が終わるときに書き起こしを控えておく。何手か移動したら忘れる。
KEEP_TRANSCRIPT_MOVES = 3

# 依頼を作る機能を出すか。False なら既存の依頼から選ぶだけになる。
ENABLE_GENERATION = True

# 個別依頼の一覧を誰が組むか。
#
#   "game"  ゲーム自身の `DisplayQuestChoice` を開く（**既定**）
#   "mod"   自前で `PhaseSpec('QuestChoiceManager', [quest_type, id])` を並べる
#
# **"mod" は既定では使わない。** `QuestChoiceManager` の `quest_type` 引数は、
# クエスト辞書の `quest_type` フィールド（`'normal_quest'` / `'random_quest'`）
# **とは別の語彙**で、セーブのフィールド値をそのまま渡すと `KeyError` になり
# ゲームが落ちる（GAME.md §2.2）。正しい値が `206_` の総当たりで判明するまで封じる。
LIST_MODE = "game"

# "mod" モードで使う quest_type。**実測で確かめた値だけを書くこと。**
# None のままなら "mod" を指定しても "game" に落ちる。
QUEST_TYPE_FOR_CHOICE = None

# 一覧に並べる既存依頼の上限。実データでは1つの集落につき3件なので
# 通常これに当たることは無い。ページ送りの挙動を実測できていない間は
# 1ページに収まる範囲に抑えておく。
MAX_LISTED = 8

# 生成に使う会話の直近何発言ぶんを渡すか。
CONVERSATION_TURNS = 12
CONVERSATION_CHARS = 2500

# 生成が終わってから受注画面に入るまでの余韻（秒）。テキストの流し込みを待つ。
SETTLE = 0.4

# 押下を横取りするための印。ボタン辞書に足す（ゲームは text と spec しか見ない）。
# **`302_` とは別のキーにすること**（共有すると、向こうの `on_button_press` が
# こちらの action を知らずに握り潰す）。
MARK = "mod_action"

# 依頼人の人物像を、生成のプロンプトに添えるか。
USE_NPC_MEMORY = True

# `311_npc_profile_memory` が覚えた人物像の置き場所。**mod を import しない。**
# ローダは mod を `instantale_mod_<フォルダ名>` で登録するので、番号を振り直した
# 瞬間に名前で掴む側が壊れる（TECH.md §3.2.3）。共有してよいのはローダの
# `instantale_modloader.*` だけなので、mod どうしは**同じ場所を読む**ことで繋ぐ。
# ファイルが無ければ何も添えない ― 向こうを切っていても、まだ一度も会話して
# いなくても成立する。
NPC_MEMORY_DIRNAME = "npc_profiles"

# 上のファイルから読む欄と、プロンプトでの見出し。向こうの `RECORD_KEYS` の
# うち本文を持つ2つ。増えたら**ここに足すのはこちらの判断**（知らない欄を
# 勝手に載せない）。
NPC_MEMORY_FIELDS = (("profile", "人物像"), ("about_player", "冒険者への態度"))

# 添える人物像の上限。依頼の生成には人物の輪郭があれば足りるので、
# 会話の書き起こしより短くする。
NPC_MEMORY_CHARS = 800

# 自前ボタンに持たせる無害な spec は `ui.SAFE_CLS`
# （`JustSetButtonToNormalPhase`）。mod 無しで押されても選択肢が戻るだけ。

# 世界ごとのファイル名は `instantale_modloader.state.world_filename` が作る。
# ここに同じ規則を写さないこと ― `311_` と1文字でも違うと**同じファイルを
# 指せなくなる**（読む側と書く側で別の名前になる）。写した版が実際にずれた
# 経緯は state.py の docstring と TECH.md §3.2.3 にある。


def _text(value, limit=200):
    if value is None:
        return ""
    if not isinstance(value, str):
        value = str(value)
    value = value.strip()
    return value if len(value) <= limit else value[:limit] + "…"


def apply(ctx):
    log_path = ctx.out_path(LOG_BASENAME)
    clients_path = ctx.state_path(CLIENTS_BASENAME)
    state = {
        "saved_buttons": None,   # 一覧を出す前のボタン。やめる で戻す
        "npc_id": None,          # いま会話している相手
        "generating": False,
        "inject": None,          # random_quest_generator に渡す会話の書き起こし
        "inject_at": 0.0,
        # 会話が終わった後も「この話から依頼を作る」を使えるようにするための控え。
        # {"text": 書き起こし, "npc_id": ..., "npc_name": ..., "moves": 残り手数}
        "last_talk": None,
        # 会話から開いた掲示板を絞り込む相手。{"npc_id":..., "npc_name":...}
        # ゲーム本来の「クエスト掲示板」から開いたときは None のままにして
        # 一切手を触れない。
        "filter_npc": None,
    }
    INJECT_TTL = 300.0

    write = ctx.logger(LOG_BASENAME)

    # 選択肢・spec の読み取り・画面の塗り替え・会話の閉じ方は
    # `instantale_modloader.ui` に集約してある（`300_` / `302_` と共有）。
    # 特に **画面を塗るのは `refresh_choice_buttons` ではなく HUD 側**という
    # `302_` の実測結果は、この mod にも要る（下の `apply_buttons`）。
    screen = ui.Screen(ctx, write, tag="quest offer", mark=MARK)

    find_app = ui.find_app
    cls_of = ui.cls_of
    spec_cls_name = ui.spec_cls_name
    spec_args = ui.spec_args
    pressed_entry = ui.pressed_entry
    say = screen.say
    refresh = screen.refresh

    def button(text, mark=None, cls_name=None, args=()):
        return screen.button(text, mark=mark, cls_name=cls_name, args=args)

    def apply_buttons(app, entries, tag):
        """選択肢を差し替えて画面に反映する。**必ず次のフレーム**で行う。

        `refresh_choice_buttons` を直接呼ぶだけでは**画面が塗り替わらない**
        （GAME.md §2.3）。要点は3つ:

          * 押下と同じ流れの中で差し替えると、ゲームがその後に描画するので
            古い内容に戻る → `Clock.schedule_once(..., 0)` で次のフレームに送る
          * 実際に塗っているのは `InstanTaleHUD.update_button_texts` で、
            `app.to_display_buttons` は監視されていない → HUD を直接呼ぶ
          * 選択肢を組むゲーム側の `execute` は別スレッドで走ることがある →
            Clock 経由なら必ずメインスレッド
        """
        screen.apply_buttons(app, entries, tag)

    class OfferPhase(object):
        """自前のフェーズ。`app.process_choice` に渡してゲームと同じ経路を通す。

        **`app.buttons` を書いて `refresh_choice_buttons()` を直接呼ぶだけでは
        画面が塗り替わらない**（GAME.md §2.3）。選択肢だけが差し替わって画面は
        古いままになり、存在しない添字が押せてしまう。

        ゲーム自身は選択肢を変えるとき必ず `process_choice(マネージャ, 文字列)`
        を通し、その中で `execute` が別スレッドに渡される。描画の面倒はその経路が
        見ているので、同じ経路に乗せる。

        **`PhaseSpec` には決して載せない。** 載せるとセーブに焼かれて、mod 無しの
        次回起動で `getattr(__main__, 'OfferPhase')` が失敗する。`process_choice`
        はインスタンスを受け取るので載せる必要も無い。
        """

        def __init__(self, app, action):
            self.app = app
            self.action = action

        def execute(self, choice_text):
            return dispatch(self.app, self.action, choice_text)

    def start_phase(app, action, choice_text):
        """ゲームの経路で自前のフェーズを起こす。使えなければ直接やる。"""
        screen.start_phase(app, OfferPhase(app, action), choice_text,
                           fallback=lambda: dispatch(app, action, choice_text))

    def dispatch(app, action, choice_text):
        if action == "offer":
            open_quest_board(app, choice_text)
        elif action == "generate":
            generate_from_conversation(app)
        elif action == "cancel":
            restore_buttons(app)
        # "busy"（待機表示の「…」）は何もしない。押しても無反応にするのが仕事。

    # ------------------------------------------------------------ 依頼の候補
    # 現在地は **エリアのオブジェクトとは限らない**（NPC 側のセーブでは `"7"` と
    # いう id の文字列だった。`302_` が形を決めつけて2度外した箇所）。
    # `ui.current_area` は id でもオブジェクトでも引き当てる。
    current_area = ui.current_area

    def candidate_quests(app):
        """この土地で受注できる依頼を (id, Quest) で返す。

        判定は `neighboring_settlement_id == 現在エリアの id` と
        `config['status'] == 'incomplete'`。セーブの実データで、依頼は
        集落ごとに 3 件ずつこのキーで束ねられていることを確認済み。
        ゲーム自身の `get_quest_difficulties(area, world)` と突き合わせて、
        食い違ったら記録する（こちらの当て推量が外れた合図）。
        """
        world = getattr(app, "world", None)
        quests = getattr(world, "quests", None)
        area = current_area(app)
        if not isinstance(quests, dict) or area is None:
            return []
        area_id = str(getattr(area, "id", ""))
        found = []
        for qid, quest in quests.items():
            config = getattr(quest, "config", None)
            if isinstance(config, dict) and config.get("status") != "incomplete":
                continue
            if str(getattr(quest, "neighboring_settlement_id", "")) != area_id:
                continue
            found.append((str(qid), quest))
        found.sort(key=lambda pair: _difficulty_of(pair[1]))

        # ゲーム自身の答えと照合する。ずれていれば設計の前提が間違っている。
        functions = sys.modules.get("scripts.functions")
        expected = getattr(functions, "get_quest_difficulties", None) if functions else None
        if expected is not None:
            try:
                theirs = sorted(expected(area, world))
                ours = sorted(_difficulty_of(q) for _, q in found)
                if theirs != ours:
                    write("WARN difficulty mismatch: game={} mod={} "
                          "(neighboring_settlement_id filter may be wrong)".format(
                              theirs, ours))
            except Exception as exc:
                write("get_quest_difficulties failed: {}: {}".format(
                    type(exc).__name__, exc))
        return found

    def _difficulty_of(quest):
        value = getattr(quest, "difficulty", 0)
        return value if isinstance(value, int) else 0

    def quest_label(quest):
        return "【{}】{}".format(_difficulty_of(quest),
                                 _text(getattr(quest, "quest_title", "依頼"), 40))

    # ------------------------------------------------- 会話の書き起こし
    def transcribe(app, npc_id):
        """`app.current_conversation_history` を生成プロンプトに載せる形にする。

        ゲーム自身が `context_manager.conversation_history_to_text` を持って
        いるので、まずそれを使う。無ければ role/content から素朴に組む。
        """
        history = getattr(app, "current_conversation_history", None)
        if not isinstance(history, list) or not history:
            return ""
        recent = history[-CONVERSATION_TURNS:]
        player = getattr(app, "player", None)
        npc = npc_of(app, npc_id)
        context_manager = sys.modules.get("scripts.llm.context_manager")
        to_text = getattr(context_manager, "conversation_history_to_text", None) \
            if context_manager else None
        if to_text is not None and npc is not None:
            try:
                text = to_text(recent, player, npc)
                if isinstance(text, str) and text.strip():
                    return text.strip()[:CONVERSATION_CHARS]
            except Exception as exc:
                write("conversation_history_to_text failed: {}: {}".format(
                    type(exc).__name__, exc))
        player_name = _text(getattr(player, "name", "プレイヤー"), 40)
        npc_name = _text(getattr(npc, "name", "NPC"), 40)
        lines = []
        for message in recent:
            if not isinstance(message, dict):
                continue
            speaker = player_name if message.get("role") == "user" else npc_name
            lines.append("{}: {}".format(speaker, _text(message.get("content"), 400)))
        return "\n".join(lines)[:CONVERSATION_CHARS]

    def current_talk(app):
        """「この話」として使える会話を (書き起こし, npc_id, NPC名) で返す。

        会話中ならその場の履歴。会話を抜けた後なら終了時に控えた分
        （施設の選択肢から入ったときはこちら）。どちらも無ければ空。
        """
        if getattr(app, "in_conversation", False) and state["npc_id"] is not None:
            text = transcribe(app, state["npc_id"])
            if text:
                npc = npc_of(app, state["npc_id"])
                return text, state["npc_id"], _text(getattr(npc, "name", ""), 40)
        last = state["last_talk"]
        if last is not None and last.get("moves", 0) > 0:
            return last["text"], last["npc_id"], last["npc_name"]
        return "", None, ""

    def remember_talk(app):
        """会話が終わる直前に書き起こしを控える。

        `finish_conversation` の**後**では `current_conversation_history` が
        片付けられている可能性がある。だから必ず元の処理より前に取る。
        """
        npc_id = state["npc_id"]
        if npc_id is None:
            return
        text = transcribe(app, npc_id)
        if not text:
            return
        npc = npc_of(app, npc_id)
        state["last_talk"] = {
            "text": text,
            "npc_id": npc_id,
            "npc_name": _text(getattr(npc, "name", ""), 40),
            "moves": KEEP_TRANSCRIPT_MOVES,
        }
        write("remembered talk with {!r} ({} chars, valid for {} moves)".format(
            state["last_talk"]["npc_name"], len(text), KEEP_TRANSCRIPT_MOVES))

    def npc_of(app, npc_id):
        characters = getattr(getattr(app, "world", None), "characters", None)
        if isinstance(characters, dict) and npc_id is not None:
            return characters.get(str(npc_id))
        return None

    # ------------------------------------ どの依頼がどの NPC 発かの控え

    def npc_memory(app, npc_id):
        """`311_npc_profile_memory` が覚えている依頼人の人物像。無ければ空文字。

        **読むだけ。書かない。**（あちらの持ち物なので、こちらが触ると
        「MOD が足したものは MOD が片付ける」が成立しなくなる。）
        ディレクトリも作らない ― `311_` を切っている人の `state/` に、
        使われない空のフォルダを置かないため（`ctx.state_path()` は親を作るので
        ここでは使わない）。置き場所を分ける前の `out/` から拾い直すのは
        あちらの仕事で、こちらは在るものを読むだけ。
        """
        if not USE_NPC_MEMORY or not npc_id:
            return ""
        path = os.path.join(ctx.state_dir, NPC_MEMORY_DIRNAME,
                            world_filename(world_key(app)))
        # 読みは `ctx.read_json` を通す。結果はどちらも「添えない」で同じだが、
        # 「無い（`311_` を入れていない・初対面）」は黙って、「**在るのに
        # 読めない**」は記録してから倒れる ― 人物像が添わらない原因が
        # 「記録がまだ無い」のか「読めなかった」のかを後から見分けられる
        # （`ctx.read_json` はディレクトリを作らないので、相手を切っている人の
        # `state/` に空のフォルダを置く心配もない）。
        data = ctx.read_json(path, None)
        record = data.get(str(npc_id)) if isinstance(data, dict) else None
        if not isinstance(record, dict):
            return ""
        lines = []
        for key, label in NPC_MEMORY_FIELDS:
            value = record.get(key)
            if isinstance(value, str) and value.strip():
                lines.append("{}: {}".format(label, value.strip()))
        return _text("\n".join(lines), NPC_MEMORY_CHARS)

    def load_clients():
        # このファイルは**全世界ぶんが1つ**なので、読めない1回を黙って {} に
        # 倒すと、次の remember_client が全世界の出所を空で書き直してしまう。
        # 「無い（初回）」だけを黙って倒し、「在るのに読めない」は記録に残す
        # （ctx.read_json）。
        data = ctx.read_json(clients_path, {})
        return data if isinstance(data, dict) else {}

    def remember_client(app, quest_id, npc_id, npc_name):
        """この依頼はこの NPC 発、と控える。セーブには触らない。"""
        data = load_clients()
        bucket = data.setdefault(world_key(app), {})
        bucket[str(quest_id)] = {"npc_id": str(npc_id) if npc_id else "",
                                 "npc_name": npc_name or ""}
        # 途中で落ちても控えが壊れない書き方（`ctx.write_json`）。素朴に
        # open(..., "w") で書くと、依頼の出所が丸ごと読めなくなる。
        # **失敗は例外ではなく戻り値で返る**（TECH.md §3.11.1）ので、try で
        # 囲っても何も捕まらない ― 囲っていた版は、書けなかった回にも
        # `remembered client` の成功ログを出していた。
        if ctx.write_json(clients_path, data):
            write("remembered client: quest {!r} <- {!r} ({})".format(
                quest_id, npc_name, npc_id))
        else:
            write("WARN could not remember the client of quest {!r} "
                  "(the offer will look like nobody's)".format(quest_id))

    def quest_belongs_to(app, quest_id, npc_id, npc_name):
        """この依頼はこの NPC のものか。

        2つの根拠を使う:

        1. **mod の控え**: 会話から生成した依頼。これが本命で確実
        2. **`client_name` の一致**: 元から世界にあった依頼のうち、たまたま
           依頼人がこの NPC である場合を拾う。ゲームが生成する依頼人名は
           実在 NPC と結びついていないことが多いので、あくまで補助
        """
        record = load_clients().get(world_key(app), {}).get(str(quest_id))
        if isinstance(record, dict):
            if npc_id and record.get("npc_id") == str(npc_id):
                return True
            if npc_name and record.get("npc_name") == npc_name:
                return True
        if npc_name:
            quest = quest_of(app, str(quest_id))
            if quest is not None:
                client = quest_value(quest, "client_name", "")
                if isinstance(client, str) and client.strip() == npc_name.strip():
                    return True
        return False

    # ============================================================ 一覧を出す
    def end_conversation_then(app, follow_up):
        """会話をゲーム自身の経路で終わらせてから `follow_up` を走らせる。

        これを飛ばして掲示板を開くと、`ConversationEndManager` が走らないまま
        会話状態（`app.in_conversation`）が残り、**NPC の立ち絵が消えずに
        付いてくる**（GAME.md §2.5）。会話は終了処理を通さないと閉じられない。
        立ち絵の片付けも関係値の更新もその中にある。

        起こし方は「画面にある『会話を終了する』ボタンの spec をそのまま使い、
        **`end_text` だけ差し替える**」。引数（`in_conversation_id` / `finisher`）を
        推測せずに済み、要約とライフログには「依頼の話のために切り上げた」が残る。
        待ちと後始末は `ui.Screen.end_conversation` が持っている。
        """
        entry = ui.find_spec_button(getattr(app, "buttons", None),
                                    "ConversationEndManager")
        if entry is None:
            write("end conversation: no ConversationEndManager button; aborting")
            say(app, "（今は依頼の話を切り出せない）")
            return
        # 会話が閉じるので、会話中のボタンへ戻る道は捨てる。
        state["saved_buttons"] = None
        screen.end_conversation(
            app, entry, follow_up, end_text=END_TEXT,
            on_abort=lambda _reason: say(app, "（今は依頼の話を切り出せない）"),
            poll=END_POLL, timeout=END_TIMEOUT)

    def open_quest_board(app, choice_text=OFFER_LABEL):
        """「依頼を受ける」が押されたとき。**ゲーム自身の掲示板**を開く。

        自前で `PhaseSpec('QuestChoiceManager', [quest_type, id])` を並べる実装は
        ゲームを落とした（冒頭の `LIST_MODE` の説明を参照）。`quest_type` の
        語彙が実測で確かめられていない以上、その組み立てはこちらの仕事ではない。

        `DisplayQuestChoice` はゲーム自身のクエスト掲示板で、一覧の組み立ても
        受注画面への受け渡しも全部持っている。そこへ渡してしまえば、正しい
        `quest_type` を知る必要が無くなる（`300_` が会話フェーズをゲーム本来の
        経路で起こしたのと同じ考え方）。
        """
        # 会話中なら、まず会話を正しく閉じる。閉じずに掲示板へ移ると
        # 立ち絵が残って付いてくる（`end_conversation_then` の説明）。
        if getattr(app, "in_conversation", False):
            write("open board: still in conversation; closing it first")
            # **会話の終了処理は `buttons_backup`（会話相手の一覧）を復元する。**
            # そのまま掲示板へ移ると、途中で NPC 一覧が一瞬見える
            # （GAME.md §2.4）。待機表示を出したまま繋いで隠す。
            # 出すのはゲーム自身と同じ点のアニメーションなので、割り込みが
            # 挟まったようには見えない。
            show_busy(app)
            end_conversation_then(app, lambda a: open_quest_board(a, choice_text))
            return

        if LIST_MODE == "mod" and QUEST_TYPE_FOR_CHOICE is None:
            write("LIST_MODE='mod' but QUEST_TYPE_FOR_CHOICE is unverified; "
                  "falling back to the game's own board")
        if LIST_MODE == "mod" and QUEST_TYPE_FOR_CHOICE is not None:
            show_mod_quest_list(app)
            return

        display_cls = cls_of("DisplayQuestChoice")
        if display_cls is None:
            write("open board: DisplayQuestChoice not found")
            say(app, "（依頼の一覧を開けなかった）")
            return
        # これは「会話から開いた掲示板」なので、その相手の依頼だけに絞る。
        # 印を立てるのはここだけ ― ゲーム本来の「クエスト掲示板」から開いた
        # ときは None のままなので、そちらは全件のまま何も変わらない。
        if FILTER_BY_NPC:
            _t, filter_id, filter_name = current_talk(app)
            if filter_id or filter_name:
                state["filter_npc"] = {"npc_id": filter_id, "npc_name": filter_name}
                write("open board: filtering for {!r} (id={!r})".format(
                    filter_name, filter_id))
        state["saved_buttons"] = list(getattr(app, "buttons", []) or [])
        # 待機表示を出したまま来ている場合はここで解く。掲示板が自分で
        # 並べ直すので、こちらの点のアニメーションが上から塗ってしまわないよう
        # **掲示板を起こす前**に止める。`restore=False` は「元の選択肢は
        # 塗り直さない」＝ NPC 一覧を出さないため。
        if screen.is_busy():
            clear_busy(app, restore=False)
        write("open board: process_choice(DisplayQuestChoice, {!r})".format(choice_text))
        try:
            app.process_choice(display_cls(app), choice_text)
        except Exception:
            ctx.log_exc("quest offer: opening the quest board failed")
            say(app, "（依頼の一覧を開けなかった）")

    def show_mod_quest_list(app):
        """自前で一覧を組む。`QUEST_TYPE_FOR_CHOICE` が実測済みのときだけ通る。"""
        entries = []
        listed = 0
        for qid, quest in candidate_quests(app):
            if listed >= MAX_LISTED:
                break
            entry = button(quest_label(quest), cls_name="QuestChoiceManager",
                           args=[QUEST_TYPE_FOR_CHOICE, qid])
            if entry is not None:
                entries.append(entry)
                listed += 1
        if not entries:
            say(app, "いま受けられる依頼は無いようだ。")
            write("mod list: nothing to show")
            return
        cancel = button(CANCEL_LABEL, mark="cancel")
        if cancel is not None:
            entries.append(cancel)
        state["saved_buttons"] = list(getattr(app, "buttons", []) or [])
        apply_buttons(app, entries, "mod list")
        write("mod list: {} quest(s) with quest_type={!r}".format(
            listed, QUEST_TYPE_FOR_CHOICE))

    def restore_buttons(app):
        saved = state["saved_buttons"]
        state["saved_buttons"] = None
        if saved is None:
            return
        apply_buttons(app, saved, "restore")

    # ======================================================= 会話から生成する
    def wait_state(app):
        """待機表示になっているかを見るための一行。生成の前後で記録する。"""
        return screen.busy_state(app)

    # ------------------------------------------------------ 待機表示（「...」）
    #
    # **ゲームがどう待機表示を出しているかは `ui.Screen` が持つ**（点のアニメ
    # ーション・`is_button_enabled`・送信ボタンの塞ぎ方。GAME.md §2.4）。
    # `305_` も同じ待ち方をするので、二重に持たない（TECH.md §6）。
    def show_busy(app):
        screen.busy_on(app)

    def clear_busy(app, restore=True):
        screen.busy_off(app, restore=restore)

    def generate_from_conversation(app):
        """ゲーム自身の生成経路を、会話の書き起こしを添えて呼ぶ。

        **別スレッドに投げてはいけない。ここで最後までやりきる。**

        別スレッドに投げて即座に戻ると、`process_choice` は「この行動は終わった」
        と判断して操作を戻すので、LLM が裏で回っている最中にプレイヤーが移動も
        会話もできてしまう。

        ゲーム自身の長い処理（会話の開始・依頼の生成）はどれも
        `process_choice` が `execute` を走らせている**間ずっと**待機表示
        （ボタンが「…」になる）を出しっぱなしにする。`process_choice` は
        メインスレッド、`execute` は別スレッドで、
        **`execute` は既に専用スレッドで走っている**。だからここで
        同期的に待っても UI スレッドは止まらないし、待機表示は
        ゲーム自身の仕組みがそのまま面倒を見る。

        ＝ **自前で待機 UI を作らない。ゲームの行動の寿命に合わせるだけ。**
        """
        if state["generating"]:
            say(app, "……いま話をまとめているところだ。")
            return
        display_cls = cls_of("DisplayQuestChoice")
        if display_cls is None:
            write("generate: DisplayQuestChoice not found")
            say(app, "（依頼を作れなかった）")
            return

        transcript, npc_id_at_start, npc_name = current_talk(app)
        if not transcript:
            write("generate: empty conversation transcript")
            say(app, "（まだ話が足りない）")
            return

        # 会話中に押されたのか（＝生成が終わっても会話に戻る）を控える。
        in_conversation = bool(getattr(app, "in_conversation", False))

        state["generating"] = True
        # 人物像はここで読んでおく。生成のフックは `execute` の別スレッドで
        # 走るので、ゲームの状態（`world_key`）を触るのはこちら側に寄せる。
        state["inject"] = {"transcript": transcript, "npc_name": npc_name,
                           "persona": npc_memory(app, npc_id_at_start)}
        state["inject_at"] = time.monotonic()
        show_busy(app)
        say(app, "――話を整理して、依頼として書き起こしている……")
        write("=" * 78)
        write("generate: npc={!r} transcript={} chars in_conversation={!r}".format(
            npc_name, len(transcript), in_conversation))

        def finish(quest_id):
            state["generating"] = False
            state["inject"] = None
            write("generate: finished -> {}".format(wait_state(app)))
            if quest_id is None:
                clear_busy(app)
                schedule(app, lambda: say(app, "（依頼にはならなかった）"))
                return
            quest = quest_of(app, quest_id)
            title = quest_value(quest, "quest_title", "依頼")
            # 依頼人はこの会話の相手にする。生成側は client_name を自由に
            # 決めてしまうので、ここで会話の相手に上書きする（両方の格納先に）。
            if npc_name:
                set_quest_value(app, quest_id, "client_name", npc_name)
            # この依頼はこの NPC 発、と mod 側に控える（セーブには触らない）。
            # 次にこの NPC と話したとき、その依頼だけを出すための根拠になる。
            remember_client(app, quest_id, npc_id_at_start, npc_name)
            write("generate: -> quest {!r} {!r} (client={!r})".format(
                quest_id, title, npc_name))
            # **受注画面へは自分で飛ばない。** `QuestChoiceManager` を自前で
            # 組み立てると `quest_type` の語彙が合わずに落ちる（冒頭の
            # `LIST_MODE` の説明）。
            if in_conversation:
                # 会話は閉じない。待機表示を解いて
                # 会話画面へ戻すだけ。依頼は世界に登録済みなので、受注は
                # 「依頼を受ける」から後でできる。
                clear_busy(app)
                schedule(app, lambda: say(
                    app, "「{}」の話がまとまった。（依頼として受けられる）".format(title)))
                return
            # 会話の外から作った場合は、ゲーム自身の掲示板を開き直す。
            # 作ったばかりの依頼もゲームが正しい spec で並べてくれる。
            clear_busy(app, restore=False)
            schedule(app, lambda: (say(app, "「{}」の話がまとまった。".format(title)),
                                   open_quest_board(app, OFFER_LABEL)))

        started = time.monotonic()
        before = set(quest_ids(app))
        try:
            display_cls(app).generate_random_quest()
        except Exception:
            ctx.log_exc("quest offer: generate_random_quest failed")
            finish(None)
            return
        # **数として並べる。** 素の sorted は辞書順なので "10" < "9" になり、
        # 1回の生成で複数増えた回だけ「いちばん新しい id」を取り違える。
        added = sorted(set(quest_ids(app)) - before, key=ui.id_sort_key)
        write("generate: took {:.1f}s; new quest ids={}".format(
            time.monotonic() - started, added))
        finish(added[-1] if added else None)

    def schedule(app, fn):
        """LLM を待った後の後始末をメインスレッドで走らせる。

        **手が空くのを待つ**（`300_` の実測を反映）。情景描写や会話の要約を
        流し込んでいる最中に `add_text` や掲示板の開き直しをすると押し流される。
        既に行動は確定しているので、待ちきれなくても実行する。
        """
        screen.when_idle(app, fn, settle=SETTLE, proceed_on_timeout=True,
                         tag="settle")

    def quest_id_of_button(entry):
        """依頼ボタンが指しているクエスト id。

        `QuestChoiceManager.__init__(self, app, quest_type, quest_id)` なので
        `args = (quest_type, quest_id)`。**読むだけ**なので、`quest_type` の
        語彙を知らなくても id は取れる。
        """
        if spec_cls_name(entry) != "QuestChoiceManager":
            return None
        args = spec_args(entry)
        if not args or len(args) < 2:
            return None
        return str(args[1])

    # ------------------- ゲームの掲示板を、その NPC 発の依頼だけに絞る＋生成を足す
    @ctx.wrap("__main__:DisplayQuestChoice.update_button_display", required=False)
    def quest_board_buttons(orig, self, *args, **kwargs):
        """掲示板が並び終えた**後**に、絞り込みと自前項目の追加をする。

        **依頼ボタンは作らず、ゲームが作ったものを間引くだけ。** `quest_type` の
        語彙を知らずに済ませるのが設計の要点なので、組み立てには絶対に回らない
        （組み立てると `quest_type` の語彙が合わずに落ちる。冒頭の `LIST_MODE`）。
        id は spec の `args[1]` から**読む**だけで取れる。

        絞るのは会話から開いたときだけ（`state["filter_npc"]`）。ゲーム本来の
        「クエスト掲示板」から開いたときは何も間引かない。
        """
        result = orig(self, *args, **kwargs)
        try:
            app = getattr(self, "app", None) or find_app()
            if app is None:
                return result
            buttons = getattr(app, "buttons", None)
            if not isinstance(buttons, list):
                write("quest board: app.buttons is {}; leaving it alone".format(
                    type(buttons).__name__))
                return result

            changed = False
            target = state["filter_npc"] if FILTER_BY_NPC else None
            if target is not None:
                npc_id = target.get("npc_id")
                npc_name = target.get("npc_name")
                kept, dropped = [], []
                for entry in buttons:
                    quest_id = quest_id_of_button(entry)
                    if quest_id is None:
                        kept.append(entry)          # 戻る等。依頼ボタン以外は残す
                        continue
                    if quest_belongs_to(app, quest_id, npc_id, npc_name):
                        kept.append(entry)
                    else:
                        dropped.append(quest_id)
                if dropped:
                    buttons[:] = kept
                    changed = True
                write("quest board: filtered for {!r} -> kept {}, dropped {}".format(
                    npc_name, sum(1 for b in kept if quest_id_of_button(b)), dropped))

            # **掲示板には「この話から依頼を作る」を出さない。** 会話画面に
            # 直接置いてあり、そちらなら会話を閉じずに生成できる。掲示板は
            # 「既にある依頼を選ぶ場所」に徹する。

            if changed:
                # **`refresh` だけでは画面が塗り替わらない**（GAME.md §2.3）。
                # しかもここはゲームが掲示板を組み終えた直後 ― 描画はもう
                # 済んでいるので、次のフレームで塗り直す必要がある。`execute` は
                # 別スレッドで走ることがあるため、Clock 経由でメインスレッドに
                # 渡すのも必須。`app.buttons` は上で直接いじってあるので、
                # 差し替えはせず塗り直しだけ頼む（entries=None）。
                apply_buttons(app, None, "quest board")
        except Exception:
            ctx.log_exc("quest offer: cannot adjust the quest board")
        return result

    # ---------------------------------------------- quests へのアクセス（両形式）
    # クエストは2箇所にある（`206_` の計測）。読むのはどちらでもよいが、
    # **書くときは必ず両方**。その作法はローダに集約してある
    # （`ui.quest_stores` ほか。`305_` / `307_` と共有。TECH.md §3.2.3）。
    quest_stores = ui.quest_stores
    quest_ids = ui.quest_ids
    quest_of = ui.quest_of
    quest_value = ui.quest_value

    def set_quest_value(app, quest_id, name, value):
        ui.set_quest_value(app, quest_id, name, value,
                           on_error=lambda msg: ctx.log_exc("quest offer: " + msg))

    # ================================================================ フック
    def has_offer_button(buttons):
        return any(isinstance(b, dict) and b.get(MARK) == "offer" for b in buttons)

    def offer_slot(buttons):
        """「依頼を受ける」を挿す位置と、そこがどの画面かを返す。

        **判定は文字列ではなく spec のクラス名で行う。** 表記や言語設定に
        依存しないし、依頼一覧そのもの（`QuestChoiceManager` が並ぶ）には
        どちらの目印も無いので入れ子にならない。

            ConversationEndManager がある → 会話画面
                実測: 会話中の app.buttons は ['会話を終了する'] の1個だけ。
                その **手前** に挿す（会話終了は最後に置きたい）
            DisplayTalkChoice がある       → 施設のルートメニュー
                会話相手を選べる場所 ＝ 施設のルート。「会話する」の **隣**
        """
        for index, entry in enumerate(buttons):
            name = spec_cls_name(entry)
            if name == "ConversationEndManager" and "conversation" in OFFER_SITES:
                return index, "conversation"
            if name == "DisplayTalkChoice" and "facility" in OFFER_SITES:
                return index + 1, "facility"
        return None, None

    def insert_generate_button(app, buttons, at, npc_name):
        """「この話から依頼を作る」を会話画面に直接置く。

        掲示板を経由しない ＝ **会話を閉じずに生成できる**。
        掲示板を開くには会話を閉じるしかないが、生成するだけなら
        その必要が無い。依頼は世界に登録されるので、受注は後から掲示板でできる。
        """
        if not ENABLE_GENERATION:
            return False
        if any(isinstance(b, dict) and screen.mark_of(b) == "generate" for b in buttons):
            return False
        label = "{}（{}）".format(GENERATE_LABEL, npc_name) if npc_name else GENERATE_LABEL
        entry = button(label, mark="generate")
        if entry is None:
            return False
        buttons.insert(max(0, min(at, len(buttons))), entry)
        return True

    def insert_offer_button(buttons, at, where):
        # 会話中は押すと会話が終わる。文言でそれが分かるようにする。
        label = CONVERSATION_OFFER_LABEL if where == "conversation" else OFFER_LABEL
        entry = button(label, mark="offer")
        if entry is None:
            return False
        buttons.insert(max(0, min(at, len(buttons))), entry)
        return True

    @ctx.wrap("__main__:InstantaleApp.refresh_choice_buttons", required=False)
    def refresh_choice_buttons(orig, self, reset_page=False, *args, **kwargs):
        """施設の選択肢に「依頼を受ける」を「会話する」の隣へ足す。

        会話中の「行動」への切り替えが画面のどこにあるか未確認なので、
        **必ず見える経路**をもう1本用意する。ボタンが並び直されるたびに
        通るので、どの画面から戻ってきても居続ける。

        施設のルートメニューかどうかは**文字列ではなく spec で**判定する。
        「`DisplayTalkChoice` を呼ぶボタンがある」＝ 会話相手を選べる場所
        ＝ 施設のルートメニュー。「会話する」という表記に依存しないので
        言語設定が変わっても効く。依頼一覧そのもの（`QuestChoiceManager` が
        並ぶ）には `DisplayTalkChoice` が無いので、入れ子にはならない。
        """
        try:
            buttons = getattr(self, "buttons", None)
            if isinstance(buttons, list) and not state["generating"]:
                # **印を失った自前ボタンの残骸を先に落とす。**
                # セーブに焼かれるのは text と spec だけで印は落ちるので、
                # タイトルへ戻る・ロード・再注入のあとは「自分のものと
                # 見なせない自分のボタン」が並んでいる。落としてから差し直す
                # ことで、二重化も「押しても無反応」も同時に消える。
                screen.prune_stale(buttons, OUR_LABELS)
                at, where = offer_slot(buttons)
                if at is not None:
                    # 会話画面には「この話から依頼を作る」も置く。掲示板を
                    # 経由しないので**会話を閉じずに生成できる**。
                    if where == "conversation":
                        _t, _id, npc_name = current_talk(self)
                        if _t and insert_generate_button(self, buttons, at, npc_name):
                            write("added {!r} to the conversation menu".format(
                                buttons[at].get("text")))
                            at += 1
                    if not has_offer_button(buttons) and \
                            insert_offer_button(buttons, at, where):
                        write("added {!r} to the {} menu ({} buttons now)".format(
                            buttons[at].get("text"), where, len(buttons)))
        except Exception:
            ctx.log_exc("quest offer: cannot add offer button")
        return orig(self, reset_page, *args, **kwargs)

    @ctx.wrap("__main__:InstantaleApp.on_button_press", required=False)
    def on_button_press(orig, self, button_index, *args, **kwargs):
        """自前のボタンだけ横取りする。

        判定に使うのは文字列ではなくボタン辞書に付けた印。同じ文字列の
        ゲーム側ボタンを巻き込まないため。印が無ければ必ず素通しする。
        """
        entry = pressed_entry(self, button_index)
        action = entry.get(MARK) if isinstance(entry, dict) else None
        if action is None:
            # ゲーム本来の「クエスト掲示板」が押されたら絞り込みを解く。
            # そちらは全件が出るべきで、こちらの都合を持ち込まない。
            if spec_cls_name(entry) == "DisplayQuestChoice" and state["filter_npc"]:
                write("filter cleared: the game's own quest board was opened")
                state["filter_npc"] = None
            return orig(self, button_index, *args, **kwargs)
        if action == "busy":
            # 待機表示の「…」。押されても何もしない ＝ 生成中は操作させない。
            write("pressed the busy placeholder; ignored")
            return None
        text = entry.get("text") or OFFER_LABEL
        write("pressed {!r} ({})".format(text, action))
        # 直接やらずにゲームの経路（process_choice）に乗せる。でないと
        # 画面が塗り替わらない ― OfferPhase の説明を参照。
        start_phase(self, action, text)
        return None

    @ctx.wrap("__main__:InstantaleApp.toggle_to_action_in_conversation", required=False)
    def toggle_to_action(orig, self, *args, **kwargs):
        """会話中の「行動」メニューが開いたら「依頼を受ける」を足す。

        ゲームがボタンを組み終えた**後**に足す。先に足すと組み直しで消える。
        この経路は切り替えが画面のどこにあるか未確認なので、出れば儲けもの
        という位置づけ。確実な経路は施設側（`refresh_choice_buttons`）。
        """
        result = orig(self, *args, **kwargs)
        if "action_menu" not in OFFER_SITES:
            return result
        try:
            buttons = getattr(self, "buttons", None)
            if not isinstance(buttons, list):
                write("toggle_to_action: app.buttons is {}; not adding".format(
                    type(buttons).__name__))
                return result
            if has_offer_button(buttons):
                return result
            if insert_offer_button(buttons, len(buttons) - 1, "conversation"):
                refresh(self)
                write("added {!r} to the in-conversation action menu "
                      "({} buttons)".format(CONVERSATION_OFFER_LABEL, len(buttons)))
        except Exception:
            ctx.log_exc("quest offer: cannot add offer button (action menu)")
        return result

    @ctx.wrap("__main__:ConversationStartManager.__init__", required=False)
    def conversation_start(orig, self, app, character_id, *args, **kwargs):
        # 誰と話しているか。生成した依頼の依頼人にする。
        state["npc_id"] = str(character_id) if character_id is not None else None
        return orig(self, app, character_id, *args, **kwargs)

    @ctx.wrap("__main__:ConversationEndManager.finish_conversation", required=False)
    def finish_conversation(orig, self, *args, **kwargs):
        """会話を閉じる**前**に書き起こしを控える。

        施設の選択肢から「依頼を受ける」に入る経路では、押された時点で
        既に会話は終わっている。`current_conversation_history` が
        片付けられた後では手遅れなので、必ず元の処理より前に取る。
        """
        try:
            app = getattr(self, "app", None)
            if app is not None:
                remember_talk(app)
        except Exception:
            ctx.log_exc("quest offer: cannot remember the talk")
        state["npc_id"] = None
        state["saved_buttons"] = None
        return orig(self, *args, **kwargs)

    @ctx.wrap("__main__:MovePhaseManager.move_phase", required=False)
    def move_phase(orig, self, *args, **kwargs):
        # 控えた会話は数手で忘れる。何十手も前の立ち話から依頼が生えると
        # 因果が繋がらないため。
        # 移動したら絞り込みは解く。その場を離れた以上、次に掲示板を開くのは
        # 別の文脈のはず。
        if state["filter_npc"] is not None:
            state["filter_npc"] = None
        last = state["last_talk"]
        if last is not None:
            last["moves"] -= 1
            if last["moves"] <= 0:
                write("forgot the talk with {!r}".format(last["npc_name"]))
                state["last_talk"] = None
        return orig(self, *args, **kwargs)

    # ------------------------------------- 生成プロンプトへ会話を差し込む
    @ctx.wrap("scripts.llm.llm_manager_world_generate:random_quest_generator",
              required=False)
    def random_quest_generator(orig, world_overview, settlement_name,
                               settlement_overview, settlement_structure_description,
                               area_description, quest_difficulty, *args, **kwargs):
        """`area_description` にこの会話の書き起こしを添える。

        引数を足すのではなく既存の自由記述欄に載せるだけなので、出力スキーマ
        （QuestStructure）にも呼び出し側にも一切影響しない。印は1回で使い切る
        ので、掲示板から普通に作られた依頼は素通しする。
        """
        mark = state["inject"]
        if mark is None:
            return orig(world_overview, settlement_name, settlement_overview,
                        settlement_structure_description, area_description,
                        quest_difficulty, *args, **kwargs)
        state["inject"] = None
        if time.monotonic() - state["inject_at"] > INJECT_TTL:
            write("inject: stale marker, left untouched")
            return orig(world_overview, settlement_name, settlement_overview,
                        settlement_structure_description, area_description,
                        quest_difficulty, *args, **kwargs)

        addition = (
            "\n\n【この依頼の発端 ― 最優先で反映すること】\n"
            "以下は依頼人「{npc}」が冒険者に持ちかけた会話の記録である。\n"
            "この会話で持ち出された困り事・頼み事をそのまま依頼の中身にすること。\n"
            "会話に出てこない別件を新たに作ってはならない。\n"
            "- client_name は必ず「{npc}」とすること。\n"
            "- request_summary は、この会話で頼まれた内容を依頼文にしたものとすること。\n"
            "- client_statement は、この会話から読み取れる依頼人の内心・事情とすること。\n"
            "- 舞台となるエリアは、会話で語られた場所に沿って設計すること。\n"
            "--- 会話の記録 ---\n{transcript}\n--- 記録ここまで ---"
        ).format(npc=mark["npc_name"] or "依頼人", transcript=mark["transcript"])

        # 過去の会話から分かっている依頼人の人となり（`311_` の控え）。
        # **この会話の記録より後ろに置く。** 依頼の中身を決めるのはあくまで
        # 今の会話で、人物像は client_statement の口ぶりを寄せるためのもの。
        persona = mark.get("persona") or ""
        if persona:
            addition += (
                "\n\n【依頼人「{npc}」について過去の会話から分かっていること】\n"
                "client_statement の口ぶりと動機をこの人物像に沿わせること。\n"
                "ただし、ここに書かれた事柄を依頼の中身にしてはならない"
                "（依頼の中身は上の会話の記録だけから決める）。\n"
                "--- 人物像 ---\n{persona}\n--- 人物像ここまで ---"
            ).format(npc=mark["npc_name"] or "依頼人", persona=persona)

        merged = (area_description or "") + addition
        write("inject: area_description {} -> {} chars (npc={!r}, persona={} chars)"
              .format(len(area_description or ""), len(merged), mark["npc_name"],
                      len(persona)))
        result = orig(world_overview, settlement_name, settlement_overview,
                      settlement_structure_description, merged,
                      quest_difficulty, *args, **kwargs)
        if isinstance(result, dict):
            write("inject: generated {!r} client={!r}".format(
                result.get("quest_title"), result.get("client_name")))
        else:
            write("inject: generator returned {}".format(repr_value(result)))
        return result

    ctx.log("quest from conversation: sites={} list={} generation={} log={}".format(
        "/".join(OFFER_SITES),
        LIST_MODE if (LIST_MODE != "mod" or QUEST_TYPE_FOR_CHOICE is not None)
        else "mod->game (quest_type unverified)",
        ENABLE_GENERATION, log_path))
