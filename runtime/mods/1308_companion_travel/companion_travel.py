# -*- coding: utf-8 -*-
"""NPC を連れて歩く。**パーティは使わない。**

ゲームには既に雇用（パーティ）があるが、あれは契約で、
`get_npc_employ_price` の金と `area_move_rejector` の関係性審査が付いて回る。
ここで足すのは**個人的な同行**で、報酬も契約も無く、戦闘にもクエストにも
参加しない。ただ付いて来るだけ。

```
会話画面 ──「同行を持ちかける」── LLM 判定 ─┬ 承諾 → 同行リストへ
                                        └ 拒否 → セリフだけ
施設間の移動 ────────────────────────── 無条件に付いて来る
土地跨ぎの移動 ─ 押下を横取り ─ LLM 判定 ─┬ 全員継続 → そのまま出発
                                        └ 誰か拒否 → 置いて行く / やめる
会話画面 ──「同行を解く」───────────── その場に残る
```

## この mod が守っている決め事

**ゲームのセーブ構造に独自キーを足さない**（TECH.md §6）。同行リストは
`out/companions.json` に置き、**世界名をキーに含める** ― `out/` は世界を跨いで
残るので、含めないと別の世界の同行者が湧く。

**`'同行中'` という関係性の語彙は使わない。** 実ログ（`output_data/`）では
これは**パーティ在籍の印**であって、こちらの同行とは別の間柄。パーティに居る
NPC にはボタンを出さず、同行リストにも入れない（移動はゲームの仕事）。

**判定は自前のプロンプトで行う。** ゲームの `area_move_rejector` は雇用契約を
前提に「関係性が深くないので拒否されるべき」と書いており、しかも**拒否されると
移動そのものが止まる**。こちらは決定権をプレイヤーに残す（置いて行くか、やめるか）。

**引数を発明しない。** 土地移動の押下を横取りしたら、押されたボタンの
`PhaseSpec` を `cls_name` / `args` のまま組み直して再発行する
（GAME.md §2.2 / §2.17）。`AreaMoveManager(app, target_area_id, mode)` の
`mode` が何なのかを知らずに、本来の押下と1バイトも変わらない移動を起こせる。

**LLM は `execute` の中で回す。** 押下を飲み込んだら自前のフェーズを
`process_choice` に渡す ― ゲームはそれを専用スレッドで走らせるので、UI
スレッドを止めずに待てる（`301_` が別スレッドに投げて操作が漏れた反省。
GAME.md §2.1）。待機表示は `ui.Screen.busy_on`。

**安全側の倒し方は場面で逆になる。**

- 同行の合意が読めない → **拒否**（勝手に付いて来ない）
- 継続の判定が読めない → **継続**（移動のたびに同行者が勝手に減るのが最悪の壊れ方）
"""

import datetime
import json
import sys
import time

from instantale_modloader import ui

# ---- 設定（既定値は mod.json の "settings" と一致させること。
#      `tools/check_mods.py` が AST で突き合わせる）------------------------
FOLLOW_ON_AREA_MOVE = True    # 土地跨ぎでも連れて行く
JUDGE_ON_AREA_MOVE = True     # 土地跨ぎで継続の可否を判定する（切ると無条件）
LEAVE_NEEDS_CONSENT = False   # 同行を解くのにも NPC の承諾を要る事にする
MAX_COMPANIONS = 3            # 同行者の上限（判定は人数分 LLM を回す）
ANNOUNCE_ARRIVAL = True       # 土地移動の後に同行者の名前を出す

LOG_BASENAME = "companion.log"
STATE_BASENAME = "companions.json"

# ボタン辞書に付ける印。**`301_` の "mod_action" / `302_` の
# "mod_party_action" とは別のキーにすること**（共有すると押下を食い合う）。
MARK = "mod_companion_action"

OFFER_LABEL = "同行を持ちかける"
RELEASE_LABEL = "同行を解く"
LEAVE_BEHIND_LABEL = "置いて行く"
ABORT_TRAVEL_LABEL = "旅をやめる"

# 自前の `manager_name`。これを付けると自分のプロンプトも
# `output_data/<世界>/<PC>/<manager_name>/N.json` に残り、判定の検証が
# オフラインでできる（GAME.md §2.12）。
# 土地移動のクラス（実測・2026-07-30。**綴りはゲームのまま**）。
#   AreaMoveCofirmation(app, target_area_id)  行き先の一覧。ボタンの文言が行き先名
#   AreaMoveManager(app, target_area_id, mode)  徒歩 'on_foot' / 馬車 'coach'
TRAVEL_CLASS = "AreaMoveManager"
CONFIRM_CLASS = "AreaMoveCofirmation"

MANAGER_JOIN = "mod_companion_join"
MANAGER_LEAVE = "mod_companion_leave"
MANAGER_CONTINUE = "mod_companion_continue"

MAX_TOKENS = 220

# 判定に載せる会話の量（`301_` と同じ考え。長すぎると要点が薄まる）。
CONVERSATION_TURNS = 12
CONVERSATION_CHARS = 2000

# 承諾の合図。1行目にこのどちらかが来る前提で読み、読めなければ既定へ倒す。
ACCEPT_WORD = "承諾"
REFUSE_WORD = "拒否"

# 同行の約束として控える書き起こしの長さ。要約のために LLM を追加で
# 呼ぶことはしない（呼べば待ち時間が倍になる）。
AGREEMENT_CHARS = 600

FAILED_TEXT = "（今はその話を切り出せない）"


def _text(value, limit=200):
    if value is None:
        return ""
    if not isinstance(value, str):
        value = str(value)
    value = value.strip()
    return value if len(value) <= limit else value[:limit] + "…"


def apply(ctx):
    log_path = ctx.out_path(LOG_BASENAME)
    state_path = ctx.out_path(STATE_BASENAME)
    state = {
        "travel": None,       # 判定中の土地移動（押されたボタンと戻り先）
        "busy": False,        # 判定の二重起動よけ
        "last_skip": None,    # ボタンを出さなかった理由（ログの重複よけ）
        "destination": "",    # 直前に選ばれた行き先の表示名（実測で確定）
        "warned_world": False,  # 世界名で控えが引けなかったことを1度だけ残す
        "opening": None,      # 会話開始直前の NPC。第一声の文脈を差し替えるためだけに使う
    }
    cache = {"data": None}    # 同行リストの控え（書くのはこの mod だけ）

    def write(text):
        try:
            with open(log_path, "a", encoding="utf-8") as fh:
                fh.write("[{}] {}\n".format(
                    datetime.datetime.now().isoformat(timespec="milliseconds"), text))
        except Exception:
            ctx.log_exc("companion: write failed")

    screen = ui.Screen(ctx, write, tag="companion", mark=MARK)
    say = screen.say

    # ------------------------------------------------------------ 世界と人物
    def world_key(app):
        """世界を見分ける名前。取れなければ '_'（1世界しか使わない前提に落ちる）。

        `out/` は世界を跨いで残るので、**これをキーに含めないと別の世界の
        同行者が湧く**。
        """
        world_dict = getattr(app, "world_dict", None)
        if isinstance(world_dict, dict):
            data = world_dict.get("world_data")
            if isinstance(data, dict):
                for key in ("world_name", "name", "title"):
                    value = data.get(key)
                    if isinstance(value, str) and value:
                        return value
        name = getattr(getattr(app, "world", None), "name", None)
        return name if isinstance(name, str) and name else "_"

    def character_of(app, npc_id):
        characters = getattr(getattr(app, "world", None), "characters", None)
        if isinstance(characters, dict) and npc_id:
            return characters.get(str(npc_id))
        return None

    def name_of(app, npc_id):
        return _text(getattr(character_of(app, npc_id), "name", ""), 40) or "その相手"

    # ------------------------------------------------------------ パーティ名簿
    # **読み取りだけ。** 名簿を書く必要はここには無い（同行はパーティと別）。
    # 規則は `302_` と同じ ― 候補を集めて `'player'` を含むものを本物とし、
    # `list` / `dict` のどちらでも読む。動いている `302_` に手を入れる риск を
    # 避けるため、共通部品には上げず読み取り専用でここに持つ（VERIFICATION.md）。
    def party_stores(app):
        stores, seen = [], set()

        def add(value):
            if not isinstance(value, (list, dict)) or id(value) in seen:
                return
            seen.add(id(value))
            stores.append(value)

        add(getattr(app, "party", None))
        variables = getattr(app, "game_variables", None)
        if isinstance(variables, dict):
            add(variables.get("party"))
        world_dict = getattr(app, "world_dict", None)
        if isinstance(world_dict, dict):
            add(world_dict.get("party"))
            inner = world_dict.get("game_variables")
            if isinstance(inner, dict):
                add(inner.get("party"))
        add(getattr(getattr(app, "world", None), "party", None))
        add(getattr(getattr(app, "player", None), "party", None))
        return stores

    def element_id(value):
        """名簿の要素を id の文字列にする。文字列でも Character でも読む。"""
        if value is None:
            return ""
        if isinstance(value, str):
            return value
        for attr in ("id", "character_id", "npc_id"):
            found = getattr(value, attr, None)
            if isinstance(found, (str, int)):
                return str(found)
        if isinstance(value, dict):
            for key in ("id", "character_id", "npc_id"):
                if key in value:
                    return str(value[key])
        return str(value)

    def store_ids(store):
        if isinstance(store, dict):
            ids = []
            for key, value in store.items():
                found = element_id(key)
                if not found or found.startswith("<"):
                    found = element_id(value)
                ids.append(found)
            return ids
        return [element_id(value) for value in store]

    def party_ids(app):
        """本物の名簿の id 一覧。名簿には必ず `'player'` が入る。"""
        candidates = [store_ids(store) for store in party_stores(app)]
        for ids in candidates:
            if "player" in ids:
                return ids
        for ids in candidates:
            if ids:
                return ids
        return []

    def in_party(app, npc_id):
        return bool(npc_id) and str(npc_id) in party_ids(app)

    # ------------------------------------------------------------ 同行リスト
    def load_all():
        """ファイル全体（世界名 -> 同行リスト）。**一度読んだら覚えておく。**

        `refresh_choice_buttons` は選択肢が組み直されるたびに呼ばれるので、
        そのたびに JSON を読み直すと描画のたびにディスクを叩くことになる。
        書くのはこの mod だけなので、書いた内容をそのまま控えれば足りる。
        """
        if cache["data"] is None:
            try:
                with open(state_path, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
            except Exception:
                data = {}
            cache["data"] = data if isinstance(data, dict) else {}
        return cache["data"]

    def save_all(data):
        cache["data"] = data
        try:
            with open(state_path, "w", encoding="utf-8") as fh:
                json.dump(data, fh, ensure_ascii=False, indent=1)
            return True
        except Exception:
            ctx.log_exc("companion: cannot write {}".format(state_path))
            return False

    def companions(app):
        """この世界の同行リスト `{npc_id: 控え}`。

        世界の見分けは名前で行っている。**名前は外部のデータ改変ツールで
        書き換えられる**ので、書き換えられた世界では控えが行方不明になり、
        同行者が黙って付いて来なくなる。安定した id は実測できていないため
        名前のままにしてあるが、**そうなったことが分かるように1度だけ残す**。
        """
        data = load_all()
        key = world_key(app)
        bucket = data.get(key)
        if not isinstance(bucket, dict):
            if data and not state["warned_world"]:
                state["warned_world"] = True
                write("no companions for world {!r}; the file has {}".format(
                    key, sorted(data)))
            return {}
        return bucket

    def is_companion(app, npc_id):
        return bool(npc_id) and str(npc_id) in companions(app)

    def mark_opening(npc_id):
        """会話開始と `conversation_starter` の間だけ相手を控える。

        `conversation_starter` の引数には相手の id が無い。開始マネージャの初期化時に
        控え、次の呼び出しで必ず消費する。TTL を過ぎた印は無視するため、別の LLM
        呼び出しへ同行の文脈が漏れない。
        """
        state["opening"] = {
            "npc_id": str(npc_id) if npc_id is not None else "",
            "at": time.monotonic(),
        }

    def add_companion(app, npc_id, agreement, reply):
        """同行を控える。**約束の中身も一緒に残す。**

        継続判定はこれを材料にする ― ゲーム側の関係性の語彙はパーティの契約を
        指しており、同行の間柄を表さないため。要約のために LLM を追加で
        呼ぶことはせず、書き起こしの末尾をそのまま控える。
        """
        data = load_all()
        bucket = data.setdefault(world_key(app), {})
        bucket[str(npc_id)] = {
            "name": name_of(app, npc_id),
            "since": datetime.datetime.now().isoformat(timespec="seconds"),
            "agreement": _text(agreement, AGREEMENT_CHARS),
            "reply": _text(reply, 200),
        }
        if save_all(data):
            write("joined: {!r} ({}) -> {} companion(s)".format(
                name_of(app, npc_id), npc_id, len(bucket)))

    def drop_companion(app, npc_id, reason):
        data = load_all()
        bucket = data.get(world_key(app))
        if not isinstance(bucket, dict) or str(npc_id) not in bucket:
            return
        bucket.pop(str(npc_id), None)
        if save_all(data):
            write("left: {!r} ({}) [{}] -> {} companion(s)".format(
                name_of(app, npc_id), npc_id, reason, len(bucket)))

    # ------------------------------------------------------------ LLM の材料
    def transcribe(app, npc_id):
        """いまの会話の書き起こし。ゲーム自身の整形関数を使う（`301_` と同じ）。"""
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

    def relationship_text(npc, player):
        """NPC 側が持っているプレイヤーとの関係。初対面なら空でよい。

        実ログでは `['初対面', '同行中']` のような**語句のリスト**が
        プロンプトに載る（GAME.md §2.5）。閾値はこちらで作らず、文面のまま渡す。
        """
        rel = getattr(npc, "relationship", None)
        if isinstance(rel, dict):
            name = getattr(player, "name", None)
            for key in ("player", name):
                if key and key in rel:
                    return _text(rel[key], 300)
            return ""
        return _text(rel, 300) if rel else ""

    def emotion_text(npc):
        """プレイヤーへの感情。ゲーム自身の整形関数がある場合だけ載せる。"""
        functions = sys.modules.get("scripts.functions")
        document = (getattr(functions, "document_emotion_scores", None)
                    if functions else None)
        scores = getattr(npc, "emotion_scores", None)
        if document is None or scores is None:
            return ""
        try:
            return _text(document(scores), 300)
        except Exception as exc:
            write("document_emotion_scores failed: {}: {}".format(
                type(exc).__name__, exc))
            return ""

    def life_log_text(app, npc):
        """ライフログ。**会話画面の外からでも読める**ので継続判定の材料になる。"""
        context_manager = sys.modules.get("scripts.llm.context_manager")
        get_text = (getattr(context_manager, "get_life_log_text", None)
                    if context_manager else None)
        if get_text is not None:
            try:
                text = get_text(app, npc)
                if isinstance(text, str) and text.strip():
                    return _text(text, 1200)
            except Exception as exc:
                write("get_life_log_text failed: {}: {}".format(
                    type(exc).__name__, exc))
        return _text(getattr(npc, "life_log", ""), 1200)

    def current_log_text(npc):
        """直近の会話の要約。**パーティ外の NPC にも溜まる**（GAME.md §2.5）。"""
        return _text(getattr(npc, "current_log", ""), 1200)

    def profile_block(app, npc_id):
        """判定のプロンプトに共通で載せる、その NPC とプレイヤーの素性。"""
        npc = character_of(app, npc_id)
        player = getattr(app, "player", None)
        world = getattr(app, "world", None)
        return (
            "【世界観】\n{worldview}\n\n"
            "【{npc_name}の情報】\n- プロフィール: {profile}\n- 人格: {personality}\n"
            "- 口調: {speech}\n- 役割: {job}\n"
            "- {player_name}との関係: {relationship}\n"
            "- {player_name}に対する感情: {emotion}\n\n"
            "【{player_name}の情報】\n- プロフィール: {player_profile}"
        ).format(worldview=_text(getattr(world, "worldview", ""), 600),
                 npc_name=name_of(app, npc_id),
                 profile=_text(getattr(npc, "profile", ""), 400),
                 personality=_text(getattr(npc, "personality", ""), 300),
                 speech=_text(getattr(npc, "speech_style", ""), 200),
                 job=_text(getattr(npc, "job", ""), 60),
                 relationship=relationship_text(npc, player) or "（記録なし）",
                 emotion=emotion_text(npc) or "（記録なし）",
                 player_name=_text(getattr(player, "name", "冒険者"), 40),
                 player_profile=_text(getattr(player, "profile", ""), 400))

    # ------------------------------------------------------------ LLM 呼び出し
    # ゲームはスロットによって推論モジュールが違う。ローカルは llama_cpp、
    # 外部 API は any_server。片方しか sys.modules に載らない（実機 2026-07-30:
    # any_server 運用中に llama_cpp だけ見ると常にフォールバック拒否になった）。
    REQUEST_MODULES = (
        "scripts.llm.request_llm_inference_llama_cpp_completion",
        "scripts.llm.request_llm_inference_any_server",
    )

    def resolve_send():
        """いま載っている推論モジュールから `send_request_with_no_structure` を拾う。"""
        for name in REQUEST_MODULES:
            module = sys.modules.get(name)
            send = (getattr(module, "send_request_with_no_structure", None)
                    if module else None)
            if send is not None:
                return send, name
        return None, None

    def ask(manager_name, messages):
        """`send_request_with_no_structure` を1回。`str` が返る（GAME.md §2.12）。"""
        send, module_name = resolve_send()
        if send is None:
            write("{}: send_request_with_no_structure unavailable "
                  "(checked {})".format(manager_name, ", ".join(REQUEST_MODULES)))
            return None
        started = time.monotonic()
        try:
            result = send(manager_name, messages, max_tokens=MAX_TOKENS)
        except Exception:
            ctx.log_exc("companion: {} failed via {}".format(
                manager_name, module_name))
            return None
        write("{}: {:.1f}s via {} -> {!r}".format(
            manager_name, time.monotonic() - started, module_name,
            _text(result, 300)))
        return result

    def read_verdict(result, default):
        """`(承諾か, セリフ)` に読み解く。**読めなければ `default` に倒す。**

        1行目に判定語、2行目以降にセリフを書かせているが、モデルは地の文を
        足してくる。`300_` の `clean_line` と同じ考えで、**判定語が単独で
        読める行だけ**を判定に使い、残りをセリフとして拾う。
        """
        if not isinstance(result, str):
            return default, ""
        lines = [line.strip() for line in result.replace("\r", "\n").split("\n")]
        lines = [line for line in lines if line]
        verdict, body = None, []
        for line in lines:
            if verdict is None:
                accepted = ACCEPT_WORD in line
                refused = REFUSE_WORD in line
                if accepted != refused:
                    verdict = accepted
                    # 判定語だけの行は捨てる。セリフを兼ねているなら残す。
                    stripped = line.replace(ACCEPT_WORD, "").replace(REFUSE_WORD, "")
                    if not stripped.strip(" 　:：。.-—「」"):
                        continue
            body.append(line)
        return (default if verdict is None else verdict), clean_line(body)

    def clean_line(body):
        """セリフを1つ取り出す。鉤括弧が無ければ付ける（`300_` と同じ）。"""
        for line in body:
            text = _text(line, 160)
            if not text:
                continue
            if not text.startswith("「"):
                text = "「" + text.lstrip("「")
            if not text.endswith("」"):
                text = text.rstrip("」") + "」"
            return text
        return ""

    # ============================================================ 同行の合意
    def judge_join(app, npc_id):
        """会話画面で「同行を持ちかける」が押されたとき。

        **同行の枠組みをこちらで決めない。** 初版は「雇用契約ではない。生活と
        仕事を置いて、個人的な信頼だけで付いて行く話」と書き、さらに「店の主人・
        役人は立場上応じない」まで指示していた。実機（2026-07-30）でこれが外れた:

            プレイヤーがギルド統括部長に話を通し、部長がギルド長へ
            「隣の土地のギルドまで冒険者に同行してやれ」と指示。ギルド長は了承し、
            会話の中でも「一緒に行く」と承諾していた。
            それでも判定は **拒否** ―「任務としてなら構わないが、個人的な信頼が
            まだ無い」。**こちらが書いた枠がそう言わせた。**

        同行の理由は世界の側にある（任務・依頼・護衛・私的な頼み）。それを決める
        のはプレイヤーと NPC の会話であって、この MOD ではない。**指針は会話を
        読ませることだけに絞り、断る理由もこちらで列挙しない。**

        初対面なら関係も感情も薄いので拒否は自然に出る。**安全側は拒否**
        （判定語が読めなければ同行させない）。
        """
        npc_name = name_of(app, npc_id)
        transcript = transcribe(app, npc_id)
        player_name = _text(getattr(getattr(app, "player", None), "name", "冒険者"), 40)

        instruction = (
            "あなたはダークファンタジーRPGの登場人物'{npc}'を演じます。\n"
            "'{player}'から「この先しばらく行動を共にしてほしい」と持ちかけられました。\n"
            "パーティへの加入や雇用の契約ではありません。同行の理由と条件は"
            "直前までの会話の中にあります（任務・依頼・護衛・私的な頼み、いずれもありえます）。\n"
            "【判断の指針】\n"
            "- **直前までの会話を最優先にすること。** そこで既に同行を承知している、"
            "あるいは同行を命じられているなら、ここでも承諾すること。\n"
            "- 職務や立場を理由に断るのは、**その同行が職務として認められていない場合だけ**に"
            "すること。上司の許しがある・任務として頼まれている・行き先が職務の範囲にある、"
            "のいずれかなら応じてよい。\n"
            "- 会話の中に断る理由が無いなら、関係と感情に照らして'{npc}'として自然な方を選ぶこと。\n"
            "- ほとんど言葉を交わしていない相手や、警戒している相手には応じないこと。\n"
            "【出力の決まり】\n"
            "- 1行目に「{accept}」か「{refuse}」だけを書くこと。\n"
            "- 2行目に'{npc}'のセリフを1文か2文、鉤括弧「」で括って書くこと。\n"
            "- 地の文・情景描写・説明・発話者名は書かないこと。"
        ).format(npc=npc_name, player=player_name,
                 accept=ACCEPT_WORD, refuse=REFUSE_WORD)

        # **会話の書き起こしだけでは足りない。** `current_conversation_history` は
        # いま開いている会話ぶんしか無いので、一度会話を閉じてから持ちかけ直すと
        # 直前の経緯が丸ごと消える（ギルド長の件は上司の許可がその前の会話に
        # あった）。閉じた会話は `current_log` / `life_log` に要約が残るので、
        # 継続判定と同じ材料をここにも載せる。
        npc = character_of(app, npc_id)
        context = (
            "{profile}\n\n"
            "【直前までの会話】\n{transcript}\n\n"
            "【最近の出来事】\n{current_log}\n\n"
            "【これまでの歩み】\n{life_log}"
        ).format(profile=profile_block(app, npc_id),
                 transcript=transcript or "（この場ではまだほとんど話していない）",
                 current_log=current_log_text(npc) or "（記録なし）",
                 life_log=life_log_text(app, npc) or "（記録なし）")

        result = ask(
            MANAGER_JOIN,
            [{"role": "user", "content": instruction + "\n\n" + context},
             {"role": "user", "content": "<行動: 同行を持ちかける>"}])
        # LLM 経路が取れないときは偽の拒否セリフを出さない。会話内容と無関係な
        # 「ここを離れるわけにはいかない」が何度も出て、判定が固まったように見える。
        if result is None:
            write("join: {!r} ({}) aborted (LLM unavailable) transcript={} chars".format(
                npc_name, npc_id, len(transcript)))
            return FAILED_TEXT
        accepted, reply = read_verdict(result, default=False)
        write("join: {!r} ({}) accepted={} transcript={} chars".format(
            npc_name, npc_id, accepted, len(transcript)))

        if accepted:
            add_companion(app, npc_id, transcript, reply)
        return reply or ("「……分かった。付いて行こう」" if accepted
                         else "「……悪いが、ここを離れるわけにはいかない」")

    # ============================================================ 同行の解除
    def judge_release(app, npc_id):
        """「同行を解く」が押されたとき。`(解けたか, セリフ)`。

        押下は明示の意思表示なので、既定では**必ず解ける**。LLM には別れの
        セリフだけを書かせる。`LEAVE_NEEDS_CONSENT` を立てると承諾判定も走るが、
        **読めなければ解ける側に倒す** ― 解けない同行が残る方が困る。

        **材料は継続判定と揃える。** 初版は素性と約束のやり取りだけを渡していた。
        実機（2026-07-30）でギルド長を隣の土地まで連れて行って解いたところ:

            「了解しました。これより同行契約を解除し、貴女を自由身分へと変更します。
            …… 次回の依頼は、無駄のないようにお願いしますね」

        老人の口調でもなく、道中の記憶も無い。約束のやり取りが48文字（任務の話）
        しか無く、**旅をした事実がプロンプトのどこにも無かった**ので、モデルは
        残っていた職務上の関係だけで埋めた。道中は `current_log` / `life_log` に
        残っているので、そちらを渡す。
        """
        npc = character_of(app, npc_id)
        npc_name = name_of(app, npc_id)
        record = companions(app).get(str(npc_id), {})
        player_name = _text(getattr(getattr(app, "player", None), "name", "冒険者"), 40)

        if LEAVE_NEEDS_CONSENT:
            rule = ("- 1行目に「{accept}」（別れに応じる）か「{refuse}」（まだ離れたくない）"
                    "だけを書くこと。\n"
                    "- 2行目に'{npc}'のセリフを1文か2文、鉤括弧「」で括って書くこと。")
        else:
            rule = "- '{npc}'のセリフを1文か2文、鉤括弧「」で括って書くこと。"

        instruction = (
            "あなたはダークファンタジーRPGの登場人物'{npc}'を演じます。\n"
            "'{npc}'はここまで'{player}'と道を共にしてきましたが、ここで別れることに"
            "なりました。別れ際に'{npc}'が返す言葉を書いてください。\n"
            "【出力の決まり】\n"
            "- '{npc}'の口調と人格のまま話すこと。\n" + rule + "\n"
            "- 地の文・情景描写・説明・発話者名は書かないこと。"
        ).format(npc=npc_name, player=player_name,
                 accept=ACCEPT_WORD, refuse=REFUSE_WORD)

        context = (
            "{profile}\n\n"
            "【同行を約束したときのやり取り】\n{agreement}\n\n"
            "【そのとき{npc}が答えたこと】\n{reply}\n\n"
            "【直前までの会話】\n{transcript}\n\n"
            "【最近の出来事】\n{current_log}\n\n"
            "【これまでの歩み】\n{life_log}"
        ).format(profile=profile_block(app, npc_id),
                 agreement=_text(record.get("agreement"), AGREEMENT_CHARS) or "（記録なし）",
                 npc=npc_name,
                 reply=_text(record.get("reply"), 200) or "（記録なし）",
                 transcript=transcribe(app, npc_id) or "（この場ではまだほとんど話していない）",
                 current_log=current_log_text(npc) or "（記録なし）",
                 life_log=life_log_text(app, npc) or "（記録なし）")

        result = ask(MANAGER_LEAVE,
                     [{"role": "user", "content": instruction + "\n\n" + context},
                      {"role": "user", "content": "<行動: 同行を解く>"}])
        agreed, reply = read_verdict(result, default=True)
        released = True if not LEAVE_NEEDS_CONSENT else agreed
        write("release: {!r} ({}) released={} (consent required={})".format(
            npc_name, npc_id, released, LEAVE_NEEDS_CONSENT))
        if released:
            drop_companion(app, npc_id, "released in conversation")
        return released, reply or "「……ここまでだな」"

    # ============================================================ 継続の判定
    def judge_continue(app, npc_id, destination, mode_text):
        """土地跨ぎの前に、同行を続けるかを訊く。`(続けるか, セリフ)`。

        材料は**約束の中身**（状態ファイル）と、約束の後に何があったか
        （`current_log` / `life_log`）と、**旅の重さ**（徒歩は3か月、馬車は 1000G）。
        関係と感情は背景としてのみ載せる ― `'同行中'` はパーティの印なので
        判定条件には使わない。

        **安全側は継続**。ここで拒否に倒すと「移動するたびに同行者が勝手に
        減る」という最も苛立つ壊れ方になる。
        """
        npc = character_of(app, npc_id)
        npc_name = name_of(app, npc_id)
        record = companions(app).get(str(npc_id), {})
        player_name = _text(getattr(getattr(app, "player", None), "name", "冒険者"), 40)

        instruction = (
            "あなたはダークファンタジーRPGの登場人物'{npc}'を演じます。\n"
            "'{npc}'はいま'{player}'に同行しています。\n"
            "'{player}'はこれから{destination}へ旅立とうとしています（{mode}）。\n"
            "この土地を離れる旅に、'{npc}'が付いて行くかどうかを決めてください。\n"
            "【判断の指針】\n"
            "- 同行を約束したときの事情がまだ生きているなら、そのまま付いて行くこと。\n"
            "- 約束の後に起きた出来事が同行の理由を壊しているなら断ること。\n"
            "- 旅の重さ（期間・費用）に耐えられない事情があるなら断ること。\n"
            "- 迷う程度なら付いて行くこと。断るのは、はっきりした理由があるときだけ。\n"
            "【出力の決まり】\n"
            "- 1行目に「{accept}」（付いて行く）か「{refuse}」（ここで別れる）"
            "だけを書くこと。\n"
            "- 2行目に'{npc}'のセリフを1文か2文、鉤括弧「」で括って書くこと。\n"
            "- 地の文・情景描写・説明・発話者名は書かないこと。"
        ).format(npc=npc_name, player=player_name,
                 destination=destination or "別の土地", mode=mode_text or "長い旅路",
                 accept=ACCEPT_WORD, refuse=REFUSE_WORD)

        context = (
            "{profile}\n\n"
            "【同行を約束したときのやり取り】\n{agreement}\n\n"
            "【そのとき{npc}が答えたこと】\n{reply}\n\n"
            "【最近の出来事】\n{current_log}\n\n"
            "【これまでの歩み】\n{life_log}"
        ).format(profile=profile_block(app, npc_id),
                 agreement=_text(record.get("agreement"), AGREEMENT_CHARS) or "（記録なし）",
                 npc=npc_name,
                 reply=_text(record.get("reply"), 200) or "（記録なし）",
                 current_log=current_log_text(npc) or "（記録なし）",
                 life_log=life_log_text(app, npc) or "（記録なし）")

        follows, reply = read_verdict(
            ask(MANAGER_CONTINUE,
                [{"role": "user", "content": instruction + "\n\n" + context},
                 {"role": "user", "content": "<状況: 土地を離れる旅に出る>"}]),
            default=True)
        write("continue: {!r} ({}) follows={} destination={!r} mode={!r}".format(
            npc_name, npc_id, follows, destination, mode_text))
        return follows, reply or "「……ここまでにしておく」"

    # ============================================================ 自前のフェーズ
    class CompanionPhase(object):
        """`app.process_choice` に渡してゲームと同じ経路を通すためのフェーズ。

        **`PhaseSpec` には決して載せない**（載せるとセーブに焼かれ、mod 無しの
        次回起動で `getattr(__main__, ...)` が失敗する）。ここに来る `execute` は
        ゲームが専用スレッドで走らせるので、LLM を同期で待ってよい。
        """

        def __init__(self, app, action, payload):
            self.app = app
            self.action = action
            self.payload = payload

        def execute(self, choice_text):
            return dispatch(self.app, self.action, self.payload, choice_text)

    def start_phase(app, action, payload, choice_text):
        screen.start_phase(app, CompanionPhase(app, action, payload), choice_text,
                           fallback=lambda: dispatch(app, action, payload, choice_text))

    def dispatch(app, action, payload, choice_text):
        try:
            if action == "offer":
                run_offer(app, payload)
            elif action == "release":
                run_release(app, payload)
            elif action == "travel":
                run_travel_check(app)
            elif action == "leave_behind":
                finish_travel(app, leave_behind=True)
            elif action == "abort_travel":
                finish_travel(app, leave_behind=False)
        except Exception:
            ctx.log_exc("companion: {} failed".format(action))
            state["busy"] = False
            screen.busy_off(app)
            say(app, FAILED_TEXT)

    # ------------------------------------------------------- 会話画面の2つ
    def run_offer(app, npc_id):
        if state["busy"]:
            return
        if in_party(app, npc_id):
            say(app, FAILED_TEXT)
            return
        state["busy"] = True
        screen.busy_on(app)
        try:
            reply = judge_join(app, npc_id)
        finally:
            state["busy"] = False
            screen.busy_off(app)
        screen.when_idle(app, lambda: say(app, reply), proceed_on_timeout=True,
                         tag="offer")

    def run_release(app, npc_id):
        if state["busy"]:
            return
        state["busy"] = True
        screen.busy_on(app)
        try:
            _released, reply = judge_release(app, npc_id)
        finally:
            state["busy"] = False
            screen.busy_off(app)
        screen.when_idle(app, lambda: say(app, reply), proceed_on_timeout=True,
                         tag="release")

    # ============================================================ 土地跨ぎ
    def travel_companions(app):
        """継続判定の対象。**パーティに居る者は除く**（移動はゲームの仕事）。

        世界から居なくなった id はここで落とす。
        """
        found = []
        for npc_id in sorted(companions(app)):
            if in_party(app, npc_id):
                continue
            if character_of(app, npc_id) is None:
                drop_companion(app, npc_id, "not in this world any more")
                continue
            found.append(npc_id)
        return found

    def destination_name(app, entry):
        """行き先の名前。**引けなければ空**（文言を場所抜きに切り替える）。

        一番確かなのは**プレイヤーが押した行き先ボタンの文言**。実測（2026-07-30）
        では徒歩／馬車の1つ手前に `AreaMoveCofirmation` の一覧が出て、そこに
        行き先の名前がそのまま並ぶ:

            [0] '灰屑の街'  AreaMoveCofirmation ['0']
            [1] '東京'      AreaMoveCofirmation ['2']

        これを控えておけば、エリア表の持ち方を知らなくてよい。取れなかった
        ときだけ id からエリア表を引く（`spec_args` は**読むだけ**で、値の意味は
        解釈しない）。
        """
        if state["destination"]:
            return state["destination"]
        args = ui.spec_args(entry) or []
        areas = ui.world_areas(app)
        for value in args:
            if isinstance(value, (str, int)):
                area = areas.get(str(value))
                name = _text(getattr(area, "name", ""), 40) if area is not None else ""
                if name:
                    return name
        return ""

    def should_intercept(app, entry):
        """この押下は土地移動か。横取りしてよい場面か。"""
        if not (FOLLOW_ON_AREA_MOVE and JUDGE_ON_AREA_MOVE):
            return False
        if state["travel"] is not None or state["busy"]:
            return False
        if ui.spec_cls_name(entry) != TRAVEL_CLASS:
            return False
        return bool(travel_companions(app))

    def begin_travel(app, entry, buttons):
        """押下を飲み込んで判定に入る。**戻り先はここで控える。**"""
        text = (entry or {}).get("text") if isinstance(entry, dict) else ""
        state["travel"] = {"entry": entry, "text": text or "",
                           "buttons": list(buttons or []), "refused": []}
        write("=" * 78)
        write("travel: intercepted {!r} cls={!r} args={!r}".format(
            _text(text, 40), ui.spec_cls_name(entry), ui.spec_args(entry)))
        start_phase(app, "travel", None, text or "出発する")

    def run_travel_check(app):
        travel = state["travel"]
        if travel is None:
            return
        state["busy"] = True
        screen.busy_on(app)
        destination = destination_name(app, travel["entry"])
        refused = []
        try:
            for npc_id in travel_companions(app):
                follows, reply = judge_continue(app, npc_id, destination, travel["text"])
                if not follows:
                    refused.append((npc_id, reply))
        finally:
            state["busy"] = False

        travel["refused"] = refused
        if not refused:
            # 全員が続ける。控えた spec をそのまま再発行して本来の移動へ。
            screen.busy_off(app, restore=False)
            resume_travel(app)
            return

        # 誰かが断った。**移動を止めるかどうかはプレイヤーが決める。**
        screen.busy_off(app, restore=False)
        leave = screen.button(LEAVE_BEHIND_LABEL, mark="leave_behind")
        abort = screen.button(ABORT_TRAVEL_LABEL, mark="abort_travel")
        if leave is None or abort is None:
            write("travel: could not build the choice buttons; going ahead")
            resume_travel(app)
            return
        names = "・".join(name_of(app, npc_id) for npc_id, _reply in refused)
        screen.apply_buttons(app, [leave, abort], "travel refusal")

        def announce():
            for _npc_id, reply in refused:
                say(app, reply)
            say(app, "（{}は、この旅には付いて来ないと言っている）".format(names))

        screen.when_idle(app, announce, proceed_on_timeout=True,
                         tag="travel refusal")

    def finish_travel(app, leave_behind):
        travel = state["travel"]
        if travel is None:
            return
        if not leave_behind:
            # やめる。控えておいた徒歩／馬車の選択肢に戻すだけ。
            write("travel: cancelled by the player")
            state["travel"] = None
            screen.apply_buttons(app, travel["buttons"], "travel cancelled")
            return
        for npc_id, _reply in travel["refused"]:
            drop_companion(app, npc_id, "left behind at the border")
        resume_travel(app)

    def resume_travel(app):
        """控えた `PhaseSpec` をそのまま起こす。**本来の押下と同じ。**

        引数は組み立てない ― ゲームが既にボタンへ載せている `cls_name` と
        `args` をそのまま使う（GAME.md §2.2）。`process_choice` はメインスレッド
        から呼ぶ（本来の押下と同じ経路にするため）。
        """
        travel = state["travel"]
        state["travel"] = None
        if travel is None:
            return

        def go():
            manager = screen.instantiate_spec(app, travel["entry"])
            if manager is None:
                write("travel: cannot rebuild the spec; restoring the choices")
                screen.apply_buttons(app, travel["buttons"], "travel failed")
                say(app, FAILED_TEXT)
                return
            write("travel: resuming {!r} cls={!r}".format(
                _text(travel["text"], 40), ui.spec_cls_name(travel["entry"])))
            screen.start_phase(app, manager, travel["text"] or "出発する")

        screen.schedule(go, 0)

    # ============================================================ 移送
    def relocate_companions(app, reason, announce=False):
        """同行者をプレイヤーのいる施設へ移す。**判定はしない。**

        置き先が引けなければ**移送を諦めて理由をログに残す**（`302_` の教訓 ―
        置き場所を決めずに動かすと NPC が世界から消える）。
        """
        ids = travel_companions(app) if app is not None else []
        if not ids:
            return
        player = getattr(app, "player", None)
        facility = getattr(player, "location", None)
        node = getattr(player, "current_node", None)
        if facility is None:
            write("relocate[{}]: the player has no location; leaving {} behind"
                  .format(reason, ids))
            return

        moved = []
        for npc_id in ids:
            character = character_of(app, npc_id)
            if is_at(character, facility):
                continue
            try:
                app.move_npc_to_facility(npc_id, character, facility, node)
                moved.append(name_of(app, npc_id))
            except Exception:
                ctx.log_exc("companion: move_npc_to_facility({!r}) failed".format(npc_id))
        if moved:
            write("relocate[{}]: {} -> {!r}".format(
                reason, moved, ui.facility_name(app, facility) or facility))
        if moved and announce and ANNOUNCE_ARRIVAL:
            screen.when_idle(app, lambda: say(app, "（{}が付いて来ている）".format(
                "・".join(moved))), proceed_on_timeout=True, tag="arrival")

    def is_at(character, facility):
        """もうその施設に居るなら動かさない（同じ移動で二度呼ばれても無害にする）。"""
        if character is None:
            return True
        here = getattr(character, "location", None)
        if here is facility:
            return True
        here_id = getattr(here, "id", None)
        return here_id is not None and here_id == getattr(facility, "id", None)

    # ================================================================ フック
    @ctx.wrap("__main__:InstantaleApp.refresh_choice_buttons", required=False)
    def refresh_choice_buttons(orig, self, reset_page=False, *args, **kwargs):
        """会話画面に同行のボタンを1つ足す。**パーティの相手には出さない。**

        判定は文字列ではなく spec のクラス名と `args[0]`（相手の id）。
        `302_` の「ここで別れる」はパーティの相手にしか出ないので、**排他に
        なって枠は4に収まる**（HUD の枠は4で固定）。
        """
        try:
            add_button(self)
        except Exception:
            ctx.log_exc("companion: cannot add the companion button")
        return orig(self, reset_page, *args, **kwargs)

    def add_button(app):
        buttons = getattr(app, "buttons", None)
        if not isinstance(buttons, list):
            return
        # 位置探しは全て**同一性**で行う。辞書の `==` 比較は同じ文字列の
        # 別ボタンを掴みうる（`302_` の教訓）。
        #
        # **見るのは会話画面のボタンだけ。** 同じ印を「置いて行く」「やめる」にも
        # 付けているので、印だけで拾うと土地移動の選択肢を掃除してしまう
        # （それらを並べた直後にゲームが `refresh_choice_buttons` を通る）。
        #
        # 印が落ちた残骸（タイトル復帰・会話中の再注入）も文言で拾う。拾わないと
        # 同じ「同行を持ちかける」が多重で並ぶ。
        mine = index_of(buttons, lambda entry: isinstance(entry, dict)
                        and entry.get(MARK) in ("offer", "release"))
        if mine is None:
            mine = index_of(buttons, lambda entry: isinstance(entry, dict)
                            and entry.get("text") in (OFFER_LABEL, RELEASE_LABEL))
        npc_id, end_entry = ui.conversation_partner(buttons)
        if not npc_id or npc_id == "player":
            # 会話画面ではない。**記録もしない** ― 施設の画面を通るたびに出て、
            # 読むべき行が埋もれる。
            if mine is not None:
                buttons.pop(mine)
            return
        reason = skip_reason(app, npc_id)
        if reason is not None:
            if mine is not None:
                buttons.pop(mine)
            if state["last_skip"] != (npc_id, reason):
                state["last_skip"] = (npc_id, reason)
                write("no button for {!r} ({}): {}".format(
                    name_of(app, npc_id), npc_id, reason))
            return

        # ラベルは同行状態で変わる。**古いボタンが残っていたら差し替える** ―
        # 承諾した直後の塗り直しでは `app.buttons` はまだこちらが挿した
        # 「同行を持ちかける」を持っており、放っておくと表示が追従しない。
        joined = is_companion(app, npc_id)
        action = "release" if joined else "offer"
        label = RELEASE_LABEL if joined else OFFER_LABEL
        if mine is not None:
            current = buttons[mine]
            if current.get("text") == label:
                # セーブ復元で独自キーだけ落ちていても、同じ選択肢を作り直さない。
                current[MARK] = action
                current["mod_companion_npc"] = npc_id
                return
            buttons.pop(mine)
        entry = screen.button(label, mark=action,
                              extra={"mod_companion_npc": npc_id})
        if entry is None:
            return
        # 「会話を終了する」の手前に置く（終了は最後に残す）。
        at = index_of(buttons, lambda existing: existing is end_entry)
        buttons.insert(len(buttons) if at is None else at, entry)

    def index_of(buttons, matches):
        for index, entry in enumerate(buttons):
            if matches(entry):
                return index
        return None

    def skip_reason(app, npc_id):
        """ボタンを出さない理由。None なら出してよい。"""
        for flag in ("in_battle", "in_colosseum_battle", "in_boss_battle"):
            if getattr(app, flag, False):
                return flag
        if in_party(app, npc_id):
            # 雇用中の相手はゲームが連れて歩く。二重管理にしない。
            return "already in the party"
        if state["busy"] or state["travel"] is not None:
            return "a companion decision is in flight"
        if not is_companion(app, npc_id) and len(companions(app)) >= MAX_COMPANIONS:
            return "already travelling with {} companion(s)".format(MAX_COMPANIONS)
        return None

    @ctx.wrap("__main__:InstantaleApp.on_button_press", required=False)
    def on_button_press(orig, self, button_index, *args, **kwargs):
        """自前のボタンと、土地移動の押下を横取りする。他は必ず素通しする。"""
        try:
            entry = ui.pressed_entry(self, button_index)
            if ui.spec_cls_name(entry) == CONFIRM_CLASS:
                # 行き先の一覧。**この文言が継続判定に載る行き先の名前になる。**
                state["destination"] = _text((entry or {}).get("text"), 40)
            action = screen.mark_of(entry)
            if action is not None:
                payload = entry.get("mod_companion_npc")
                write("pressed {!r} ({}) npc={!r}".format(
                    _text(entry.get("text"), 40), action, payload))
                start_phase(self, action, payload, entry.get("text") or "")
                return None
            if should_intercept(self, entry):
                begin_travel(self, entry, getattr(self, "buttons", None))
                return None
        except Exception:
            ctx.log_exc("companion: cannot handle the press")
        return orig(self, button_index, *args, **kwargs)

    @ctx.wrap("__main__:ConversationStartManager.__init__", required=False)
    def conversation_start_init(orig, self, app, character_id, *args, **kwargs):
        """次の第一声が誰との会話かだけを控える。ゲームの状態は変えない。"""
        mark_opening(character_id)
        return orig(self, app, character_id, *args, **kwargs)

    @ctx.wrap("scripts.llm.llm_manager:conversation_starter", required=False)
    def conversation_starter(orig, messages, *args, **kwargs):
        """同行者には再会ではなく、道中の会話として第一声を書かせる。

        ゲームが保持する `messages` は書き換えない。最後の行（本来は
        `<行動: 話しかける>`）をコピーして置き換えるだけなので、会話履歴・要約・
        関係性の更新には影響しない。
        """
        opening = state["opening"]
        state["opening"] = None
        if (not isinstance(opening, dict)
                or time.monotonic() - opening.get("at", 0) > 30):
            return orig(messages, *args, **kwargs)
        npc_id = opening.get("npc_id")
        app = ui.find_app()
        if app is None or not is_companion(app, npc_id):
            return orig(messages, *args, **kwargs)
        if not isinstance(messages, list) or not messages:
            return orig(messages, *args, **kwargs)
        last = messages[-1]
        if not isinstance(last, dict) or "content" not in last:
            return orig(messages, *args, **kwargs)
        npc_name = name_of(app, npc_id)
        player_name = _text(getattr(getattr(app, "player", None), "name", "冒険者"), 40)
        replacement = dict(last)
        replacement["content"] = (
            "<状況: '{npc}'は現在'{player}'に同行しており、直前まで同じ旅を続けている。"
            "これは初対面や再会ではない。『また会えた』『久しぶり』などと扱わず、"
            "道中で自然に交わす第一声を述べよ>"
        ).format(npc=npc_name, player=player_name)
        write("opening: companion {!r} ({}) -> {!r}".format(
            npc_name, npc_id, _text(replacement["content"], 100)))
        return orig(messages[:-1] + [replacement], *args, **kwargs)

    @ctx.wrap("__main__:MovePhaseManager.move_phase", required=False)
    def move_phase(orig, self, *args, **kwargs):
        """施設間の移動。**判定はしない**（頻度が高い）。無条件に連れて行く。

        `orig` の**復帰後**に置く。復帰した時点で到着が確定している
        （`300_` の実測）。`300_` の到着イベントは `orig` 直後に話者を決めて
        いるので、外側のこちらが移送する前に決定が済んでいる ＝ 同行 NPC が
        到着イベントの話者に選ばれることはない（`after` で順序を宣言）。
        """
        result = orig(self, *args, **kwargs)
        try:
            relocate_companions(getattr(self, "app", None) or ui.find_app(),
                                "facility move")
        except Exception:
            ctx.log_exc("companion: relocation after the facility move failed")
        return result

    @ctx.wrap("__main__:AreaMoveManager.method_1", required=False)
    def area_move_method_1(orig, self, *args, **kwargs):
        """土地跨ぎの移動。**到着が確定するのはここ**（実測・2026-07-30）。

        ```
        before method_1: location='嘆きの村 - 出口'(38) area='1'
        show_loading_text()               「・」「・・」「・・・」
        after  method_1: location='鉄鎖の町 - 入口'(52) area='2'
        ```

        `execute` は `method_1` を呼んでから戻るだけなので、そちらに仕掛ける
        必要は無い。`is_at` があるので二度呼ばれても無害。
        """
        result = orig(self, *args, **kwargs)
        try:
            if FOLLOW_ON_AREA_MOVE:
                relocate_companions(getattr(self, "app", None) or ui.find_app(),
                                    "area move", announce=True)
        except Exception:
            ctx.log_exc("companion: relocation after method_1 failed")
        return result

    ctx.log("companion travel: judge_on_area_move={} max={} state={}".format(
        JUDGE_ON_AREA_MOVE, MAX_COMPANIONS, state_path))
