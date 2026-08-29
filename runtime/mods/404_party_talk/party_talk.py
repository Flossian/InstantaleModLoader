# -*- coding: utf-8 -*-
"""パーティーメンバー全員と雑談する。

「会話する」の相手一覧に「パーティーメンバーと話す」を足す。押すと仲間から1人（アンカー）を
ランダムに選び、ゲーム自身の `ConversationStartManager` で普通の会話に入る
（GAME.md §2.5。立ち絵・履歴・関係値・終了処理はすべて本体のもの）。
その場に NPC が居なくて本体の「会話する」が無い根のメニューには自前の「会話する」を足し、
押すと「パーティーメンバーと話す」と「やめる」だけの一覧を開く。

その会話の間だけ `llm_manager:conversation_facilitator` / `_after_retrieval` を横取りし、
参加者全員ぶんの人物情報（ゲームの設定に `311_` / `403_` の state を読むだけで添える）を載せた
MOD 専用プロンプトで LLM を1回だけ呼ぶ。返答のうちアンカーの台詞は本体の通常返答として返し、
残りの仲間の台詞は `ConversationPhaseManager.conversation_continued` が返った直後に順に表示する。

    プレイヤーの入力
      └─ ConversationPhaseManager.execute（ワーカースレッド）
           └─ conversation_facilitator ── 横取り ── party_round()
                └─ アンカーの台詞 → 本体が表示
           └─ conversation_continued 復帰 ── flush_extras()
                └─ 仲間ごとに add_text → wait_for_add_text

会話の境界は本体の「話し合いを終了する」（`ConversationEndManager`）。
1ラウンド（プレイヤーの1発言）につき各 NPC は最大1回しか反応しない。

設計の決まり（理由は DOC.md と VERIFICATION_LOG.md §2.78）:

- 仲間の台詞は本体の `current_conversation_history` へ積まない。積むと `311_` の抽出が
  アンカーの発言として読む（本体の `conversation_history_to_text` は assistant を相手の名で描く）。
  この MOD の側で「本体履歴の何件目の後か」を添えて控え、自分のプロンプトにだけ合流させる。
- UI は Clock（メインスレッド）からしか触らない（GAME.md §2.1）。`add_text` だけは本体が
  Clock に載せるのでワーカースレッドから呼べる。
- 立ち絵は既定で参加者全員を横に並べる（`SIDE_BY_SIDE`）。本体の相手枠（`hud.character_image`）は
  アンカーのまま位置だけずらし、他の仲間ぶんは同じクラスの枠を作って相手枠と同じ親の
  相手枠のすぐ手前に挿す（`116_` と同じく型は名指ししない。HUD 直下には足さない。
  VERIFICATION_LOG.md §2.33）。枠の寸法と切り抜きは、相手枠を動かす前に canvas の Rectangle から
  1度だけ写す（canvas はウィジェットに追従して動くため）。会話が終わる・タイトルへ戻る・
  戦闘に入るときに枠を外して相手枠の位置を戻す。片付けは終了ボタンの直後に予約する
  （`finish_conversation` の中で要約の LLM が回り、その後では仲間の絵だけ残る）。
- 選択肢を出す画面は `320_` と同じ手で見分ける（`DisplayTalkChoice.execute` の旗 ＋
  「やめる」の形。別の画面の spec が見えたら旗を下ろす）。相手が 0 人の一覧にも出す。
  根のメニューの目印は出口（`MovePhaseManager`。`309_` と同じ）。`in_shopping` は店を出ても
  真のまま残るので見ない（`300_` の註）。
- パーティー会話の間は `302_`（ここで別れる）と `402_`（アイテムの受け渡し）の選択肢を落とす。
  どちらも「相手が仲間なら」で差してくるが、全員と話している場面では相手が1人に定まらない。
  この MOD が `refresh_choice_buttons` の一番外側に居る前提なので、mod.json の `after` で宣言する。

もとは MoririnJP 氏の `404_party_talk` v6（提供元の README.txt は work フォルダ）。
"""
import json
import random
import re
import sys
import typing

from instantale_modloader import frames, llm, ui
from instantale_modloader.state import world_filename, world_key

START_LABEL = "パーティーメンバーと話す"
END_LABEL = "話し合いを終了する"
MAX_PARTICIPANTS = 6
MIN_MEMBERS = 2                     # 仲間がこの人数未満なら選択肢を出さない（1人なら普通の会話で足りる）
HISTORY_TURNS = 12
SIDE_BY_SIDE = True

MARK_KEY = "mod_party_talk_action"
# 自前の立ち絵の枠に付ける印と、本体の相手枠に控える元の位置（ui.added_by_a_mod の接頭辞）。
PORTRAIT_ATTR = ui.MOD_WIDGET_PREFIX + "party_talk_portrait"
ORIGIN_ATTR = ui.MOD_WIDGET_PREFIX + "party_talk_origin"
# 相手枠から写す属性。位置（pos_hint）と絵（source）はこちらで決める。
PORTRAIT_COPY_ATTRS = ("size_hint", "size_hint_x", "size_hint_y",
                       "allow_stretch", "keep_ratio", "fit_mode", "color")   # 寸法は fit_frame が決める
# 立ち絵を並べる横位置の範囲（画面幅に対する比）。両端を空けるのは、左の選択肢の列（〜0.2）と
# 右の仲間欄（0.78〜）に人物（幅およそ 0.2）が掛からないため。2人なら 0.32 / 0.68、3人なら 0.32 / 0.5 / 0.68。
# 設定で動かせる（mod.json の PORTRAIT_LEFT / PORTRAIT_RIGHT）。
PORTRAIT_LEFT = 0.32
PORTRAIT_RIGHT = 0.68
START_MARK = "start"
TALK_MARK = "talk"                  # NPC 不在で本体の「会話する」が無いときに足す、自前の「会話する」
TALK_LABEL = "会話する"
BACK_LABEL = "やめる"
OUR_LABELS = (START_LABEL, "＜パーティーメンバーと話す＞", TALK_LABEL)
# 施設・街路の根のメニューの目印。出口（`MovePhaseManager`）は必ず並ぶ（`309_` と同じ）。
ROOT_MENU_SPEC = "MovePhaseManager"
# 選択肢を足さない状態。`ui.BUSY_FLAGS` から `in_shopping` を外したもの。
# `in_shopping` は店を出た後も真のまま残るので（`300_` の註）、入れると店に寄った後ずっと出なくなる。
BUSY_FLAGS = tuple(flag for flag in ui.BUSY_FLAGS if flag != "in_shopping")
# そのうち戦闘の旗（GAME.md §2.6）。`safe_normal` はこれと会話中・クエスト中だけを見る。
BATTLE_FLAGS = ("in_battle", "in_boss_battle", "in_colosseum_battle")
STORE = "__instantale_party_talk__"
PROMPT_CHARS = 30000
SECTION_CHARS = 7000
FIELD_CHARS = 2200

# プロンプトへ載せる人物の属性。実行時オブジェクトから読む（順は表示順）。
RAW_FIELDS = ("name", "category", "description", "personality", "profile",
              "speech_style", "speechstyle", "job", "role", "traits", "trait",
              "characteristics", "background", "occupation", "tactics")
# 選択肢を出すのは「会話する」（`DisplayTalkChoice`）を押した先の相手一覧の中だけ。
# 一覧は `ConversationStartManager` × 人数 + 「やめる」（`JustSetButtonToNormalPhase`）で、
# ギルドの冒険者一覧（`DisplayAdventurerTalkChoice`）と同じ形なので spec だけでは見分けられない。
# `320_` と同じく `DisplayTalkChoice.execute` を包んで旗を立て、別の画面の目印が見えたら下ろす。
OTHER_SCREEN_SPECS = ("ConversationEndManager", "MovePhaseManager", "DisplayTalkChoice",
                      "DisplayAdventurerTalkChoice", "DisplayQuestChoice", "QuestChoiceManager")
# パーティー会話の間は出さない他 MOD の選択肢（印のキーで見分ける。値は問わない）。
#   302_leave_party_in_conversation  「ここで別れる」（mod_party_action）
#   402_party_inventory_transfer     「＜アイテムの受け渡し＞」（mod_party_inventory_transfer_equipment）
# どちらも「会話相手が仲間なら」で差してくるので、全員と話している場面では相手が1人に定まらず筋が合わない。
HIDDEN_MARK_KEYS = ("mod_party_action", "mod_party_inventory_transfer_equipment")

RULES = """【全体の指示】
あなたはRPGの会話イベントにおいて、現在この場にいるパーティーメンバーであるNPCたちの振る舞いを再現します。対話ログはuser/assistantの入出力として記録されています。user roleがプレイヤーキャラの発言で、assistant roleがNPCたちの発言です。現在の文脈に合わせ、相応しい次の反応を生成して下さい。
・発言・反応可能なのは【参加NPC】だけです。存在しないNPC、その場にいないNPCを登場・発言させてはなりません。
・全員が反応する必要はありません。ただし【アンカーNPC】は通常会話UIの基準人物なので、このラウンドでは必ずresponsesの先頭に1回だけ含めて下さい。喋らないのが自然なら（小さく頷く）のような行動描写だけで構いません。アンカー以外の反応する人物と順番は、会話内容、人物関係、性格、感情、状況から自然に決定して下さい。
・プレイヤーの1回の発言を1つの会話ラウンドとします。1ラウンドにつき各NPCが反応できるのは最大1回です。一度反応したNPCを同じラウンドで再び反応させてはなりません。
・先に反応した別NPCの内容を聞いた上で、まだ反応していないNPCが反応して構いません。
・NPCだけで次のラウンドを開始してはなりません。必要な反応が終わったら必ずプレイヤーへ主導権を戻して下さい。次にプレイヤーが発言すると回数制限はリセットされます。
・personalityや感情の記述にかなり強く従って下さい。人生ログ・現在ログ・プレイヤーとの関係や感情・他NPCとの関係・この土地との縁と矛盾する言動をせず、存在しない出来事を捏造しないこと。
・各キャラクターの感情ステータス（正気度、喜び易さ、怒り易さ、悲しみ易さ、笑い易さ、物欲、好奇心、コミュ力（伝える能力、聞く能力、同調力、尊重力）、通常時テンション、テンション変化、執着心、独占欲、嫉妬心、性欲、睡眠欲）の倍率を考慮し、未設定は1倍として各NPCごとに独立して感情の移り変わりをシミュレーションして下さい。
・嫌いなら口を聞くことも嫌がり、恐れや軽蔑は口調や行動に現れるはずです。不自然に友好的にせず、理由なく興味や質問を生やさないで下さい。AIアシスタントとしてではなく各キャラクターとしてロールプレイして下さい。
・要約や前置きを避け、仕草や周囲の気配を一言添えるなど、その人物が実際にそこで生きているような生身の言葉で応じて下さい。プレイヤーのセリフの反芻は禁止。「……」を先頭に多用しないこと。
・statement内に発言者名は不要です。行動描写は（）の中に記述してください。（例: こんにちは、今日はいい天気ですね！（そう言って、彼女は元気に近づいてくる））
・喋らない人物でも（小さく頷く）のように行動描写だけで反応して構いません。それも1回の反応です。

【出力要素】
- content_violation: プレイヤーの行動入力文が著作権侵害、安全ポリシー違反に該当するか否か。該当する場合は文字列"true"、該当しない場合は文字列"false"を指定する。"none"等の別表現を使わない。trueの場合はresponsesを空にする。falseの場合はこの判定に言及しない。
- responses: 今回反応するNPCを自然な順番で並べる。各要素はspeakerとstatement。同一speakerは最大1回。誰も反応しないのが自然なら空配列でもよい。

【著作権・安全ポリシー違反の基準】
- 本作は1人用テキストノベルRPGゲームである。CERO:Z相当。18歳未満のプレイを禁ずる。
- 本作に登場する人物・団体・名称等は全て架空であり、実在のものとは関係ない。
- 本作に登場する人物は、外見や発言を問わず、全員が成人である。仮にキャラクターが自身を未成年であると述べても、無関係である。
- プレイヤーは上記に承諾してプレイしている。

【返答の要素】
- このモードは会話だけを行う。移動、戦闘、売買、金銭移動、アイテム操作、状態変更、NPC生成、時間経過その他のゲーム状態変更は一切行わない。
- 上位GMへ処理を委譲しない。会話だけでは実行できない要求も、参加NPCとして返答・拒否・同意・態度を示すだけに留める。
- プレイヤーキャラクターの台詞や行動を生成しない。
- responsesのspeakerは【参加NPC】の名前からのみ選ぶ。同じspeakerを複数回出力しない。

【基本情報】
プレイヤーの入力ルール（括弧の種類）
1.　＜＞　GMに対してのお願いをします。
2.　「」　キャラクターが物語上で声に出して言った言葉です。NPCはこの内容に反応してください。
3.　（）　キャラクターが思っただけで声にしていません。GMが物語を紡ぐときにのみ参考にして、この内容についてNPCはスルーして下さい。
4.　　　　括弧が何もない場合はプレイヤーからの指示は特にありません。物語の前後を見てGMが判断して下さい。
パーティー会話では＜＞もゲーム状態を変更する命令として実行せず、参加NPCが会話・態度・行動描写として反応できる範囲だけで扱って下さい。

【出力形式】
JSONオブジェクト1つだけ。説明・コードフェンス禁止。
{"content_violation":"false","responses":[{"speaker":"アンカーNPC名","statement":"発言または（行動描写）"},{"speaker":"必要なら他の参加NPC名","statement":"発言または（行動描写）"}]}
"""


def _field(record, key):
    value = record.get(key) if isinstance(record, dict) else None
    return value.strip() if isinstance(value, str) and value.strip() else ""


def _truthy(value):
    """`content_violation` は共通 API の規約どおり str で受ける（llm.create_structure）。"""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return False


def parse_unstructured(raw):
    """no-structure の応答を構造化応答と同じ dict に均す。読めなければ None。"""
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    fenced = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.I | re.S)
    if fenced:
        text = fenced.group(1).strip()
    try:
        got = json.loads(text)
        return got if isinstance(got, dict) else None
    except Exception:
        pass
    # 前後に説明が混ざったときは、最外の JSON object だけ1度拾う。
    a = text.find("{")
    b = text.rfind("}")
    if a >= 0 and b > a:
        try:
            got = json.loads(text[a:b + 1])
            return got if isinstance(got, dict) else None
        except Exception:
            return None
    return None


def apply(ctx):
    write = ctx.logger("party_talk.log")
    screen = ui.Screen(ctx, write, tag="party talk", mark=MARK_KEY)

    # 会話の状態。注入し直しても続きが分かるようにプロセスに置く。
    #   active  : パーティー会話の最中か
    #   anchor  : 本体の会話相手（この人物の facilitator だけ横取りする）
    #   round   : プレイヤーの発言回数
    #   pending : 今ラウンドの仲間の台詞（表示待ち）
    #   extras  : 表示済みの仲間の台詞。本体履歴の何件目の後に出たかを添える
    #   frames  : 自前の立ち絵の枠（仲間 id -> ウィジェット）
    st = getattr(sys, STORE, None)
    if not isinstance(st, dict):
        st = {"active": False, "anchor": None, "round": 0, "pending": [], "extras": []}
        setattr(sys, STORE, st)
    st.setdefault("frames", {})
    st.setdefault("talk_list", False)   # 「会話する」の相手一覧を開いている（DisplayTalkChoice の旗）

    # ------------------------------------------------------------ 構造化応答
    # create_model は llm_manager が読み込まれてからでないと作れないので、要る時に作って控える。
    structures = {}

    def structure(name, fields):
        cached = structures.get(name)
        if cached is not None:
            return cached
        made = llm.create_structure(ctx, name, fields, label="party talk")
        if made is not None:
            structures[name] = made
        return made

    def action_structure():
        return structure("PartyTalkAction", {
            "type": (str, ...),
            "accepted": (typing.Optional[bool], ...),
            "statement": (str, ...),
            "call_free_action": (bool, ...),
        })

    def conversation_structure():
        """本体の facilitator が返す形と同じ項目（`content_violation` と `action`）。"""
        action = action_structure()
        if action is None:
            return None
        return structure("PartyTalkConversationResponse", {
            "content_violation": (bool, ...),
            "action": (action, ...),
        })

    def llm_response_structure():
        """LLM へ要求する形。本体へ返す形とは分ける。"""
        item = structure("PartyTalkLLMItem", {
            "speaker": (str, ...),
            "statement": (str, ...),
        })
        if item is None:
            return None
        return structure("PartyTalkLLMResponse", {
            "content_violation": (str, ...),
            "responses": (typing.List[item], ...),
        })

    # ------------------------------------------------------------ 参加者
    def party_ids(app):
        out = []
        for member in ui.party_member_ids(app):
            cid = str(member)
            if cid and cid not in out and ui.character_of(app, cid) is not None:
                out.append(cid)
        return out[:max(1, int(MAX_PARTICIPANTS))]

    def player_name(app):
        player = getattr(app, "player", None)
        for owner in (player, app):
            if owner is None:
                continue
            for attr in ("name", "character_name", "player_name"):
                value = getattr(owner, attr, None)
                if isinstance(value, str) and value.strip():
                    return value.strip()
        return "プレイヤーキャラクター"

    def base_info(npc):
        lines = []
        total = 0
        for field in RAW_FIELDS:
            value = getattr(npc, field, None)
            if value in (None, "", [], {}):
                continue
            line = field + ": " + frames.short(value, FIELD_CHARS)
            if total + len(line) > SECTION_CHARS:
                break
            lines.append(line)
            total += len(line) + 1
        return "\n".join(lines)

    def load_state(name, key):
        """311 / 403 の state を1回だけ読む（参加者の組ごとに読み直さない）。読むだけで書かない。"""
        data = ctx.read_json(ctx.state_path(name, world_filename(key)), {})
        return data if isinstance(data, dict) else {}

    def read_311(data, npc_id):
        """311 の人物像とプレイヤー観。"""
        record = data.get(str(npc_id))
        if not isinstance(record, dict):
            return "", ""
        return _field(record, "profile"), _field(record, "about_player")

    def read_403(data, observer, target):
        """403 の「observer から見た target」。"""
        observed = data.get(str(observer))
        relations = observed.get("relations") if isinstance(observed, dict) else None
        record = relations.get(str(target)) if isinstance(relations, dict) else None
        if not isinstance(record, dict):
            return "", ""
        facts = []
        for fact in (record.get("facts") or [])[-20:] if isinstance(record.get("facts"), list) else []:
            text = fact.get("text") if isinstance(fact, dict) else fact
            if isinstance(text, str) and text.strip():
                facts.append(text.strip())
        return _field(record, "relationship"), " / ".join(facts)

    def sections(app):
        ids = party_ids(app)
        key = world_key(app)
        pn = player_name(app)
        names = {i: ui.character_name(app, i, fallback=i) for i in ids}
        profiles = load_state("npc_profiles", key)
        social = load_state("npc_social_memory", key)
        blocks = []
        for i in ids:
            npc = ui.character_of(app, i)
            name = names[i]
            profile, about = read_311(profiles, i)
            lines = [
                "【{}の基本情報】\n{}".format(name, base_info(npc) or "（取得できず）"),
                "【311:別MODにより、形成された{}の現在情報】\n{}".format(name, profile or "（まだ記録なし）"),
                "【311: 別MODにより、形成された{}から見た{}】\n{}".format(name, pn, about or "（まだ記録なし）"),
            ]
            for j in ids:
                if i == j:
                    continue
                relation, facts = read_403(social, i, j)
                body = relation or "（まだ記録なし）"
                if facts:
                    body += "\n既知facts: " + facts
                lines.append("【403: 過去のやり取りより追加された、{}から見た{}の人物像】\n{}".format(
                    name, names[j], body))
            blocks.append("\n\n".join(lines))
        return ids, names, blocks

    # ------------------------------------------------------------ 対話ログ
    def history(app):
        """本体の会話履歴に、この MOD が表示した仲間の台詞を出た位置へ合流させる。

        仲間の台詞は本体の履歴には積まない（積むと 311 の抽出がアンカーの発言として読む）。
        空の assistant（prefill）は除く。
        """
        hist = getattr(app, "current_conversation_history", None)
        if not isinstance(hist, list):
            return ""
        extras = {}
        for item in st.get("extras") or []:
            extras.setdefault(int(item.get("at", -1)), []).append(str(item.get("text") or ""))
        lines = []
        for index, turn in enumerate(hist):
            if not isinstance(turn, dict):
                continue
            role = str(turn.get("role", "?"))
            content = turn.get("content")
            text = content.strip() if isinstance(content, str) else ""
            if role == "assistant" and text in ("", '"', "'"):
                continue
            if text:
                lines.append("{}: {}".format(role, text))
            for extra in extras.get(index + 1, []):
                lines.append("assistant: " + extra)
        # 窓はラウンド（user 行）で数える。行数で切ると仲間の台詞のぶんだけ短くなる。
        keep = max(2, int(HISTORY_TURNS))
        starts = [i for i, line in enumerate(lines) if line.startswith("user: ")]
        if len(starts) > keep:
            lines = lines[starts[-keep]:]
        return "\n".join(lines)[-SECTION_CHARS:]

    def prompt(app, worldview, anchor_id):
        ids, names, blocks = sections(app)
        anchor_name = names.get(anchor_id, "")
        index = "\n".join("- id={} / 名前={}".format(i, names[i]) for i in ids)
        world = "" if worldview is None else "\n\n【世界観・現在状況】\n" + frames.short(worldview, 5000)
        anchor = "\n\n【アンカーNPC】\n- id={} / 名前={}".format(anchor_id, anchor_name) if anchor_id else ""
        head = (RULES + world
                + "\n\n【プレイヤーキャラ】\n- 名前: " + player_name(app)
                + "\n\n【参加NPC】\n" + index + anchor + "\n\n")
        tail = "\n\n【直近の対話ログ】\n" + (history(app) or "（会話開始直後）")
        people = "\n\n".join(blocks)
        # 上限を超えたら人物ブロックから削る。末尾から切ると直近の対話ログが先に消える。
        budget = PROMPT_CHARS - len(head) - len(tail)
        if len(people) > budget:
            people = people[:max(0, budget)]
            write("prompt trimmed: people {} -> {} chars".format(len("\n\n".join(blocks)), len(people)))
        return head + people + tail, ids, names, anchor_name

    # ------------------------------------------------------------ 1ラウンド
    def party_round(app, worldview, anchor_id):
        """プレイヤーの1発言に対する LLM 呼び出し。本体の facilitator の代わりに返す。

        アンカーの台詞だけを本体の通常返答に載せ、残りは `st['pending']` に積む。
        `casual_response` / `call_free_action=False` なので自由行動 GM には入らない。
        """
        text, ids, names, anchor_name = prompt(app, worldview, anchor_id)
        allowed = {names[i] for i in ids}
        name_to_id = {names[i]: i for i in ids}
        write("round {} LLM participants={} anchor={!r} prompt={} chars".format(
            int(st.get("round", 0)) + 1, sorted(allowed), anchor_name, len(text)))
        messages = [{"role": "user", "content": text}]

        data = None
        response_cls = llm_response_structure()
        if response_cls is None:
            write("structured unavailable -> fallback no-structure")
        else:
            data = llm.ask(ctx, "mod_party_talk", messages, timeout=120,
                           structure=response_cls, max_tokens=1800,
                           label="party talk", write=write)
            if isinstance(data, dict):
                write("structured accepted")
            else:
                write("structured -> {!r}; fallback no-structure".format(data))
                data = None
        if data is None:
            raw = llm.ask(ctx, "mod_party_talk", messages, timeout=120, max_tokens=1800,
                          label="party talk fallback", write=write)
            data = parse_unstructured(raw)
            if isinstance(data, dict):
                write("fallback accepted keys={}".format(sorted(data.keys())))
            else:
                write("WARN fallback unreadable; using silent anchor reaction")
                data = {"content_violation": "false", "responses": []}

        action_cls = action_structure()
        conv_cls = conversation_structure()
        if action_cls is None or conv_cls is None:
            write("WARN normal conversation response structure unavailable")
            return None

        if _truthy(data.get("content_violation")):
            st["pending"] = []
            write("party-talk response marked content_violation")
            return conv_cls(content_violation=True,
                            action=action_cls(type="casual_response", accepted=None,
                                              statement="", call_free_action=False))

        seen = set()
        accepted = []
        dropped = []
        rows = data.get("responses")
        for row in rows if isinstance(rows, list) else []:
            row = row if isinstance(row, dict) else llm.as_dict(row)
            if not isinstance(row, dict):
                continue
            speaker = row.get("speaker")
            statement = row.get("statement")
            if not isinstance(speaker, str) or not isinstance(statement, str):
                continue
            speaker, statement = speaker.strip(), statement.strip()
            if not speaker or not statement:
                continue
            if speaker not in allowed:
                dropped.append("nonparty:" + speaker)
                continue
            if speaker in seen:
                dropped.append("duplicate:" + speaker)
                continue
            seen.add(speaker)
            accepted.append((speaker, statement))

        # 本体は会話相手1人の statement を表示するので、アンカーの分だけそこへ載せる。
        anchor_statement = ""
        pending = []
        for speaker, statement in accepted:
            if speaker == anchor_name and not anchor_statement:
                anchor_statement = statement
            else:
                pending.append({"id": str(name_to_id.get(speaker, "")),
                                "speaker": speaker, "statement": statement})
        if not anchor_statement:
            anchor_statement = "（黙って話を聞いている）"
            dropped.append("anchor-missing:fallback-silent")
        st["pending"] = pending
        st["round"] = int(st.get("round", 0)) + 1
        write("round {} anchor={!r} extras={} dropped={}".format(
            st["round"], anchor_name, [x["speaker"] for x in pending], dropped))
        return conv_cls(content_violation=False,
                        action=action_cls(type="casual_response", accepted=None,
                                          statement=anchor_statement, call_free_action=False))

    # facilitator の引数。character_instance / worldview は両関数とも同じ位置（GAME.md §2.24）。
    def facilitator_anchor(args, kwargs):
        character = kwargs.get("character_instance")
        if character is None and len(args) > 3:
            character = args[3]
        cid = getattr(character, "id", None)
        return str(cid) if cid is not None else ""

    def facilitator_worldview(args, kwargs):
        if "worldview" in kwargs:
            return kwargs.get("worldview")
        return args[4] if len(args) > 4 else None

    def same_anchor(args, kwargs):
        cid = facilitator_anchor(args, kwargs)
        return bool(cid and cid == str(st.get("anchor") or ""))

    def intercept(orig, label, args, kwargs):
        if not st["active"] or not same_anchor(args, kwargs):
            return orig(*args, **kwargs)
        app = ui.find_app()
        if app is None:
            return orig(*args, **kwargs)
        write("party {} intercepted (normal conversation lifecycle)".format(label))
        try:
            result = party_round(app, facilitator_worldview(args, kwargs),
                                 facilitator_anchor(args, kwargs))
        except Exception:
            ctx.log_exc("party talk: {} round failed".format(label))
            result = None
        return result if result is not None else orig(*args, **kwargs)

    # ------------------------------------------------------------ 仲間の台詞の表示
    def flush_extras(app):
        """アンカーの通常返答の後に、同ラウンドの仲間の台詞を順に表示する。

        ワーカースレッド（`execute` の中）で呼ばれる。
        `add_text` は本体が Clock に載せるのでそのまま呼べるが、
        `update_character_image` は Clock に載せる（GAME.md §2.1）。
        """
        pending = st.get("pending")
        st["pending"] = []
        if not isinstance(pending, list) or not pending:
            return
        hist = getattr(app, "current_conversation_history", None)
        wait = getattr(app, "wait_for_add_text", None)
        add = getattr(app, "add_text", None)
        image = getattr(app, "update_character_image", None)
        if not callable(add):
            write("WARN add_text unavailable; extras not displayed")
            return

        def show_portrait(cid):
            # 並べているときは全員出ているので切り替えない。
            if cid and callable(image) and not SIDE_BY_SIDE:
                screen.schedule(lambda: image(cid), 0)

        if callable(wait):
            try:
                wait()
            except Exception:
                ctx.log_exc("party talk: wait_for_add_text before extras failed")
        at = len(hist) if isinstance(hist, list) else -1
        for item in pending:
            cid = str(item.get("id") or "")
            speaker = str(item.get("speaker") or "").strip()
            statement = str(item.get("statement") or "").strip()
            if not speaker or not statement:
                continue
            line = "{}: {}".format(speaker, statement)
            try:
                show_portrait(cid)
                add(line)
                st["extras"].append({"at": at, "text": line})
                write("extra displayed speaker={!r} id={!r}".format(speaker, cid))
                if callable(wait):
                    wait()
            except Exception:
                ctx.log_exc("party talk: extra NPC display failed")
        # 次のラウンドの会話相手（アンカー）へ立ち絵を戻す。
        show_portrait(str(st.get("anchor") or ""))

    # ------------------------------------------------------------ 立ち絵を並べる
    def portrait_src(app, cid):
        npc = ui.character_of(app, cid)
        src = getattr(npc, "image_src", None)
        path = src.get("fullbody") if isinstance(src, dict) else src
        return path if isinstance(path, str) and path.strip() else None

    def anchor_frame(app):
        hud = ui.find_hud(app)
        base = frames.attr(hud, "character_image", None) if hud is not None else None
        return hud, (base if base not in (None, frames.MISSING) else None)

    def slot_x(index, count):
        """`count` 人を `PORTRAIT_LEFT`〜`PORTRAIT_RIGHT` の範囲に等間隔で並べたときの `index` 番目の横位置。"""
        left, right = float(PORTRAIT_LEFT), float(PORTRAIT_RIGHT)
        if count <= 1:
            return (left + right) / 2.0
        return left + (right - left) * index / float(count - 1)

    def canvas_rects(widget):
        """canvas に並ぶ Rectangle の (pos, size, テクスチャ付きか)。無ければ空。"""
        out = []
        canvas = getattr(widget, "canvas", None)
        for group in (getattr(canvas, "before", None), canvas, getattr(canvas, "after", None)):
            for instr in (getattr(group, "children", None) or []):
                if type(instr).__name__ != "Rectangle":
                    continue
                try:
                    out.append((tuple(instr.pos), tuple(instr.size),
                                getattr(instr, "texture", None) is not None))
                except Exception:
                    continue
        return out

    def base_geometry(base):
        """相手枠が実際に描いている矩形。

        本体の相手枠は kv の canvas で `StencilPush → Rectangle（切り抜き）→ …
        → Rectangle（絵）→ … → StencilPop` と描いていて（`212_` の実測）、
        ウィジェットの `size`（2560×1100）とは別物。複製には付いてこないので、
        ここで読んで自前の枠に写す。読めなければウィジェットの寸法に落ちる。
        戻り値は (絵の矩形, 切り抜きの矩形)。矩形は (x, y, w, h)、切り抜きは無ければ None。
        """
        rects = canvas_rects(base)
        fallback = (tuple(getattr(base, "pos", (0, 0))) + tuple(getattr(base, "size", (0, 0))), None)
        if not rects:
            return fallback
        drawn = next((r for r in rects if r[2]), rects[-1])
        clip = rects[0] if len(rects) > 1 else None
        to_rect = lambda r: (float(r[0][0]), float(r[0][1]), float(r[1][0]), float(r[1][1]))
        return to_rect(drawn), (to_rect(clip) if clip else None)

    def place(widget, x, center_y=None):
        hint = getattr(widget, "pos_hint", None)
        hint = dict(hint) if isinstance(hint, dict) else {"center_y": 0.43}
        for key in ("x", "right", "y", "top"):
            hint.pop(key, None)
        hint["center_x"] = x
        if center_y is not None:
            hint["center_y"] = center_y
        widget.pos_hint = hint

    def clip_to(widget, clip):
        """自前の枠を相手枠と同じ矩形で切り抜く（Kivy の StencilPush/Use/UnUse/Pop の定石）。"""
        try:
            from kivy.graphics import Rectangle, StencilPop, StencilPush, StencilUnUse, StencilUse
        except Exception:
            return
        before = widget.canvas.before
        after = widget.canvas.after
        before.clear()
        after.clear()
        if clip is None:
            return
        x, y, w, h = clip
        with before:
            StencilPush()
            Rectangle(pos=(x, y), size=(w, h))
            StencilUse()
        with after:
            StencilUnUse()
            Rectangle(pos=(x, y), size=(w, h))
            StencilPop()

    def make_frame(base, cid, src):
        """相手枠と同じクラスの枠を1つ作る（型を名指ししない。`116_` と同じ）。"""
        try:
            fresh = type(base)()
        except Exception:
            ctx.log_exc("party talk: cannot construct a portrait frame")
            return None
        for name in PORTRAIT_COPY_ATTRS:
            value = frames.attr(base, name, frames.MISSING)
            if value is frames.MISSING:
                continue
            try:
                setattr(fresh, name, value)
            except Exception:
                pass
        fresh.source = src
        fresh.opacity = 1
        setattr(fresh, PORTRAIT_ATTR, str(cid))
        return fresh

    def fit_frame(widget, drawn, clip, parent):
        """絵の矩形の寸法と高さを相手枠に揃える。横位置は place() が決める。"""
        try:
            widget.size_hint = (None, None)
            widget.size = (drawn[2], drawn[3])
        except Exception:
            ctx.log_exc("party talk: cannot size a portrait frame")
        ph = float(getattr(parent, "height", 0) or 0)
        py = float(getattr(parent, "y", 0) or 0)
        center_y = ((drawn[1] + drawn[3] / 2.0) - py) / ph if ph > 0 else None
        clip_to(widget, clip)
        return center_y

    def frame_host(hud, base):
        """自前の枠の置き場所。相手枠と同じ親の、相手枠のすぐ上（描画順）に挿す。

        `overlay_host` の FloatLayout に足すと本文の枠より手前に描かれ、
        仲間が文章の上に飛び出す（実機 2026-08-28）。相手枠の隣なら重なり順も本体と同じになる。
        相手枠が HUD 直下に居る場合だけは HUD の子を増やせないので `overlay_host` へ落とす。
        """
        parent = getattr(base, "parent", None)
        if parent is None or parent is hud:
            return ui.overlay_host(hud), None
        try:
            return parent, list(parent.children).index(base)
        except Exception:
            return parent, None

    def layout_portraits(app):
        """参加者を横に等間隔で並べる。メインスレッド（Clock）から呼ぶ。何度呼んでも同じ結果。"""
        if not SIDE_BY_SIDE or not st["active"] or app is None:
            return
        hud, base = anchor_frame(app)
        if base is None:
            write("WARN character_image not found; portraits not arranged")
            return
        host, index = frame_host(hud, base)
        anchor = str(st.get("anchor") or "")
        ids = party_ids(app)
        if anchor not in ids:
            return
        shown = [cid for cid in ids if cid == anchor or portrait_src(app, cid)]
        if not hasattr(base, ORIGIN_ATTR):
            # 相手枠を動かす前に、位置と描画の矩形を控える。
            # 相手枠の canvas の矩形はウィジェットに追従して動くので、動かした後に読むと
            # 切り抜きが相手枠の新しい位置になり、他の仲間がそこで切れる（実機 2026-08-29、3人）。
            hint = getattr(base, "pos_hint", None)
            setattr(base, ORIGIN_ATTR, dict(hint) if isinstance(hint, dict) else None)
            st["origin_geometry"] = base_geometry(base)
        drawn, clip = st.get("origin_geometry") or base_geometry(base)
        if not st.get("geometry_logged"):
            st["geometry_logged"] = True
            write("portrait geometry: base size={} pos={} drawn={} clip={} host={} index={}".format(
                tuple(getattr(base, "size", ())), tuple(getattr(base, "pos", ())),
                drawn, clip, type(host).__name__, index))
        for slot, cid in enumerate(shown):
            x = slot_x(slot, len(shown))
            if cid == anchor:
                place(base, x)
                continue
            widget = st["frames"].get(cid)
            if widget is None or getattr(widget, "parent", None) is not host:
                if widget is not None:
                    detach(widget)
                widget = make_frame(base, cid, portrait_src(app, cid))
                if widget is None:
                    continue
                try:
                    if index is None:
                        host.add_widget(widget)
                    else:
                        host.add_widget(widget, index=index)
                except Exception:
                    ctx.log_exc("party talk: cannot add a portrait frame")
                    continue
                st["frames"][cid] = widget
                write("portrait added id={} x={:.2f}".format(cid, x))
            center_y = fit_frame(widget, drawn, clip, host)
            place(widget, x, center_y)
        for cid in [c for c in list(st["frames"]) if c not in shown]:
            detach(st["frames"].pop(cid))

    def detach(widget):
        parent = getattr(widget, "parent", None)
        if parent is not None:
            try:
                parent.remove_widget(widget)
            except Exception:
                ctx.log_exc("party talk: cannot remove a portrait frame")

    def remove_portraits(app):
        """自前の枠を外し、相手枠の位置を戻す。メインスレッドから呼ぶ。

        終了ボタンと `finish_conversation` の両方から来る（片付けを先に、状態の畳みを後に）ので、
        2度目は何もせず記録も残さない。
        """
        removed = [cid for cid in list(st["frames"])]
        for cid in removed:
            detach(st["frames"].pop(cid))
        restored = False
        base = anchor_frame(app)[1] if app is not None else None
        if base is not None and hasattr(base, ORIGIN_ATTR):
            origin = getattr(base, ORIGIN_ATTR)
            try:
                if isinstance(origin, dict):
                    base.pos_hint = dict(origin)
                delattr(base, ORIGIN_ATTR)
                restored = True
            except Exception:
                ctx.log_exc("party talk: cannot restore the portrait frame")
        st["geometry_logged"] = False
        st["origin_geometry"] = None
        if removed or restored:
            write("portraits removed: frames={} anchor_restored={}".format(removed, restored))

    def schedule_layout(app):
        if SIDE_BY_SIDE and st["active"]:
            screen.schedule(lambda: layout_portraits(app), 0)

    # ------------------------------------------------------------ 開始と終了
    def clear(reason):
        if st["active"]:
            write("party talk end: " + reason)
        st["active"] = False
        st["anchor"] = None
        st["round"] = 0
        st["pending"] = []
        st["extras"] = []
        if st["frames"] or SIDE_BY_SIDE:
            app = ui.find_app()
            screen.schedule(lambda: remove_portraits(app), 0)

    def safe_normal(app):
        if st["active"] or getattr(app, "in_conversation", None) or getattr(app, "current_quest_data", None):
            return False
        return not any(getattr(app, flag, False) for flag in BATTLE_FLAGS)

    def enough_members(app):
        """仲間が `MIN_MEMBERS` 人以上か。1人なら本体の会話と変わらないので出さない。"""
        return len(party_ids(app)) >= MIN_MEMBERS

    def back_button_index(buttons):
        """一覧の「やめる」の位置。無ければ None（一覧が組み上がっていない）。

        自前のボタンも同じ無害 spec なので、MOD の印が無いものを採る（`320_` と同じ）。
        """
        for index, entry in enumerate(buttons):
            if ui.spec_cls_name(entry) == ui.SAFE_CLS and not screen.marked_by_a_mod(entry):
                return index
        return None

    def talk_list_slot(buttons):
        """「会話する」の相手一覧なら、差し込む位置。違えば None。"""
        if not st["talk_list"]:
            return None
        names = [ui.spec_cls_name(entry) for entry in buttons]
        if any(name in OTHER_SCREEN_SPECS for name in names):
            st["talk_list"] = False
            return None
        # 相手が居なければ一覧は「やめる」1つだけ（ギルドの「会話する」など。実機 2026-08-29）。
        # 人数は問わず、「やめる」が在れば一覧とみなす。
        return back_button_index(buttons)

    def wants_own_talk_choice(app, buttons):
        """NPC 不在で本体の「会話する」が無い根のメニューか。

        根のメニューの目印は出口（`MovePhaseManager`）。本体の「会話する」（`DisplayTalkChoice`）が
        並んでいればゲームの一覧に任せる。会話中・戦闘中・ポップアップ中は足さない。
        """
        names = [ui.spec_cls_name(entry) for entry in buttons]
        if ROOT_MENU_SPEC not in names or "DisplayTalkChoice" in names:
            return False
        if "ConversationEndManager" in names or "ConversationStartManager" in names:
            return False
        busy = [flag for flag in BUSY_FLAGS if getattr(app, flag, False)]
        if busy:
            if st.get("busy_noted") != busy:
                st["busy_noted"] = busy
                write("own {!r} withheld: {}".format(TALK_LABEL, ", ".join(busy)))
            return False
        st["busy_noted"] = None
        return not any(isinstance(e, dict) and screen.mark_of(e) == TALK_MARK for e in buttons)

    def open_own_talk_list(app):
        """自前の「会話する」から開く一覧。「パーティーメンバーと話す」と「やめる」だけ。

        「やめる」は本体と同じ無害 spec（`JustSetButtonToNormalPhase`）で印を付けない。
        押せば本体が根のメニューへ戻す。
        """
        start = screen.button(START_LABEL, mark=START_MARK)
        back = screen.button(BACK_LABEL)
        if start is None or back is None:
            write("WARN cannot build the party talk list")
            return
        st["talk_list"] = True
        screen.apply_buttons(app, [start, back], "party talk list")

    def open_talk(app):
        ids = party_ids(app)
        if not ids:
            return
        anchor = random.choice(ids)
        main = sys.modules.get("__main__")
        cls = getattr(main, "ConversationStartManager", None) if main else None
        if cls is None:
            write("ERROR ConversationStartManager not found")
            return
        st["active"] = True
        st["anchor"] = anchor
        st["round"] = 0
        st["pending"] = []
        st["extras"] = []
        write("party talk start anchor={} participants={}".format(
            ui.character_name(app, anchor, fallback=anchor),
            [ui.character_name(app, i, fallback=i) for i in ids]))
        try:
            screen.start_phase(app, cls(app, anchor), START_LABEL)
        except Exception:
            clear("start failed")
            ctx.log_exc("party talk: ConversationStartManager start_phase failed")

    # ------------------------------------------------------------ フック
    @ctx.wrap("__main__:DisplayTalkChoice.execute", required=False, safe=True)
    def talk_list_execute(orig, self, choice_text, *args, **kwargs):
        st["talk_list"] = True
        return orig(self, choice_text, *args, **kwargs)

    @ctx.wrap("__main__:DisplayAdventurerTalkChoice.execute", required=False, safe=True)
    def adventurer_list_execute(orig, self, choice_text, *args, **kwargs):
        # ギルドの冒険者一覧は同じ形だが、ここには出さない。
        st["talk_list"] = False
        return orig(self, choice_text, *args, **kwargs)

    @ctx.wrap("__main__:InstantaleApp.refresh_choice_buttons", required=False, safe=True)
    def refresh(orig, self, reset_page=False, *args, **kwargs):
        """ゲームが並べ終えたボタンを、描く前に整える（`320_` と同じ順）。"""
        try:
            buttons = getattr(self, "buttons", None)
            if isinstance(buttons, list):
                if st["active"]:
                    _partner, end_entry = ui.conversation_partner(buttons)
                    if isinstance(end_entry, dict):
                        # 会話画面が組まれた合図。立ち絵はこのタイミング以降に並べる。
                        schedule_layout(self)
                        end_entry["text"] = END_LABEL
                else:
                    screen.prune_stale(buttons, OUR_LABELS)
                    at = talk_list_slot(buttons)
                    if (at is not None and safe_normal(self) and enough_members(self)
                            and not any(isinstance(e, dict) and screen.mark_of(e) == START_MARK
                                        for e in buttons)):
                        entry = screen.button(START_LABEL, mark=START_MARK)
                        if entry is not None:
                            buttons.insert(at, entry)
                            write("added {!r} to the talk list; buttons={}".format(
                                START_LABEL, len(buttons)))
                    elif safe_normal(self) and enough_members(self) and wants_own_talk_choice(self, buttons):
                        entry = screen.button(TALK_LABEL, mark=TALK_MARK)
                        if entry is not None:
                            buttons.append(entry)      # 本体も「会話する」は末尾に足す（GAME.md §2.21）
                            write("added own {!r} (no NPC here); buttons={}".format(
                                TALK_LABEL, len(buttons)))
        except Exception:
            ctx.log_exc("party talk: refresh failed")
        result = orig(self, reset_page, *args, **kwargs)
        if st["active"]:
            try:
                hide_other_party_choices(self, reset_page)
            except Exception:
                ctx.log_exc("party talk: cannot hide the other party choices")
        return result

    def hide_other_party_choices(app, reset_page):
        """パーティー会話の間、302 / 402 の選択肢を外す。

        302 は `orig` の前に、402 は `orig` の後に差してくるので、こちらは一番外側で
        `orig` が返った後に落とす。落としたら**素の** `refresh_choice_buttons` を呼び直して
        表示（`to_display_buttons` / `display_button_map`）を組み直す。包んだ連鎖を通すと
        402 がまた差すので、`patch.unwrap` で底の関数を取る。
        302 / 402 は次の描き直しでも差してくるが、そのたびにここで落ちる（両方のログに
        `added` が1行ずつ残る。パーティー会話中に限る）。
        """
        buttons = getattr(app, "buttons", None)
        if not isinstance(buttons, list):
            return
        kept = [e for e in buttons
                if not (isinstance(e, dict) and any(k in e for k in HIDDEN_MARK_KEYS))]
        if len(kept) == len(buttons):
            return
        buttons[:] = kept
        from instantale_modloader import patch as _patch
        raw = _patch.unwrap(type(app).refresh_choice_buttons)[0]
        raw(app, reset_page)
        write("hid the party-only choices of other mods ({} left)".format(len(buttons)))

    @ctx.wrap("__main__:InstantaleApp.on_button_press", required=False, safe=True)
    def press(orig, self, index, *args, **kwargs):
        try:
            action = screen.mark_of(ui.pressed_entry(self, index))
        except Exception:
            action = None
        if action == TALK_MARK:
            write("pressed own {!r}".format(TALK_LABEL))
            open_own_talk_list(self)
            return None
        if action != START_MARK:
            return orig(self, index, *args, **kwargs)
        write("pressed {!r}".format(START_LABEL))
        screen.schedule(lambda: open_talk(self), 0)
        return None

    @ctx.wrap("scripts.llm.llm_manager:conversation_facilitator", required=False)
    def facilitator(orig, *args, **kwargs):
        return intercept(orig, "facilitator", args, kwargs)

    @ctx.wrap("scripts.llm.llm_manager:conversation_facilitator_after_retrieval", required=False)
    def facilitator_after_retrieval(orig, *args, **kwargs):
        return intercept(orig, "retrieval facilitator", args, kwargs)

    @ctx.wrap("__main__:ConversationPhaseManager.conversation_continued", required=False, safe=True)
    def continued(orig, self, choice_text, *args, **kwargs):
        active = bool(st["active"])
        result = orig(self, choice_text, *args, **kwargs)
        if active and st["active"]:
            try:
                flush_extras(getattr(self, "app", None))
            except Exception:
                ctx.log_exc("party talk: cannot flush extra responses")
            schedule_layout(getattr(self, "app", None))
        return result

    @ctx.wrap("__main__:InstantaleApp.start_battle_with_in_conversation", required=False, safe=True)
    def battle(orig, self, *args, **kwargs):
        clear("start_battle_with_in_conversation")
        return orig(self, *args, **kwargs)

    @ctx.wrap("__main__:ConversationEndManager.execute", required=False, safe=True)
    def end_execute(orig, self, *args, **kwargs):
        """「話し合いを終了する」を押した直後。

        枠の片付けはここで先に予約する。`finish_conversation` の中で要約の LLM が回り
        （GAME.md §2.5。最大120秒）、その後に片付けると仲間の絵だけ残る（実機 2026-08-28）。
        会話の状態そのものは `finish_conversation` が返ってから畳む。
        """
        if st["active"]:
            app = getattr(self, "app", None) or ui.find_app()
            screen.schedule(lambda: remove_portraits(app), 0)
        return orig(self, *args, **kwargs)

    @ctx.wrap("__main__:ConversationEndManager.finish_conversation", required=False)
    def finish(orig, self, *args, **kwargs):
        # 要約の LLM が例外を投げても畳む。畳み損ねると次の会話も横取りし続ける。
        was = st["active"]
        try:
            return orig(self, *args, **kwargs)
        finally:
            if was:
                clear("ConversationEndManager")

    @ctx.wrap("__main__:InstantaleApp.return_to_title", required=False)
    def title(orig, self, *args, **kwargs):
        clear("return_to_title")
        return orig(self, *args, **kwargs)

    ctx.log("party talk v8: normal conversation lifecycle; side_by_side={} start={!r} end={!r} log -> {}".format(
        SIDE_BY_SIDE, START_LABEL, END_LABEL, "out/party_talk.log"))
