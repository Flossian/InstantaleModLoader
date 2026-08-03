# -*- coding: utf-8 -*-
"""会話から NPC の素性を書き足していく。**ゲームの素のプロフィールは消さない。**

ゲームが NPC に持たせている `profile` / `personality` は世界生成の時に決まり、
その後の会話でどれだけ人となりが見えても増えない。人生ログ（`life_log`）は
出来事の記録で、「何が好きか」「誰と繋がっているか」を引くようには出来ていない。

```
    プレイヤーが1行入力
        │
        ├─ conversation_facilitator ── 注入 ── NPC の profile に控えを足す
        │        │
        │        └─ NPC の返答
        │
        └─ 返答が描画された次のフレーム
                 └─ 自前 LLM ── 人物像を更新 ── out/npc_profiles/<世界名>.json
```

## この mod が守っている決め事

**ゲームのセーブ構造に独自キーを足さない**（TECH.md §6）。控えは
`out/npc_profiles/<世界名>.json` に置く ― `out/` は世界を跨いで残るので、
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
止めない。続けて来たターンは直列に処理し、後の更新は前のプロフィールを読む。

**安全側の倒し方は「変更しない」。** 捏造された素性が永続化されるのが最悪の
壊れ方なので、空応答・LLM 不在・例外は既存プロフィールを維持する。
"""

import copy
import datetime
import json
import os
import queue
import re
import sys
import threading
import time

from instantale_modloader import ui

# ---- 設定（既定値は mod.json の "settings" と一致させること。
#      `tools/check_mods.py` が AST で突き合わせる）------------------------
CONVERSATION_TURNS = 8     # 抽出に載せる直近のやり取りの数
INJECT_CHARS = 1200        # 抽出LLMへの目標長（要約で収める。保存・注入では切らない）

LOG_BASENAME = "npc_profile.log"
STATE_DIRNAME = "npc_profiles"

# ファイル名に使えない文字（Windows 禁則＋制御文字）。世界名そのものは鍵に残す。
_UNSAFE_FILENAME = re.compile(r'[\\/:*?"<>|\x00-\x1f]')

# 自前の `manager_name`。これを付けると自分のプロンプトも
# `output_data/<世界>/<PC>/<manager_name>/N.json` に残り、抽出の検証が
# オフラインでできる（GAME.md §2.12）。
MANAGER_EXTRACT = "mod_npc_profile_extract"

# 旧版 `slots` の読み取り順。移行にだけ使い、新しいプロフィールは分類しない。
LEGACY_SLOTS = ("好み", "嫌悪", "経歴", "人間関係", "目標", "秘密", "約束")

# 新情報が無いときの応答。モデルが説明を足した場合も下の語を含めば変更しない。
NO_CHANGE = "変更なし"
EMPTY_WORDS = ("なし", "特になし", "不明", "無し", "該当なし", "none", NO_CHANGE)

PROFILE_HEADING = "【会話から形成された追加プロフィール】"

# 抽出に載せる書き起こしの長さ（`306_` と同じ考え。長すぎると要点が薄まる）。
CONVERSATION_CHARS = 2000


def _text(value, limit=200):
    if value is None:
        return ""
    if not isinstance(value, str):
        value = str(value)
    value = value.strip()
    return value if len(value) <= limit else value[:limit] + "…"


def safe_world_filename(world_key):
    """世界名を `out/npc_profiles/` 配下のファイル名にする。拡張子 `.json` 付き。"""
    name = world_key if isinstance(world_key, str) else ""
    name = _UNSAFE_FILENAME.sub("_", name.strip()).rstrip(". ")
    if not name or name in (".", ".."):
        name = "_"
    if len(name) > 120:
        name = name[:120]
    return name + ".json"


def apply(ctx):
    log_path = ctx.out_path(LOG_BASENAME)
    state_dir = os.path.join(ctx.out_dir, STATE_DIRNAME)
    state = {
        "warned_world": False,  # 世界名で控えが引けなかったことを1度だけ残す
        "last_inject": None,    # 直前に書いた注入の結末（同じ理由を繰り返さない）
        "last_extract_skip": None,  # 抽出を始められない理由の連続重複を抑える
        "worker": None,         # 抽出専用ワーカー（仕事が無ければ終了する）
    }
    cache = {"buckets": {}}     # 世界名 -> 控え（書くのはこの mod だけ）
    jobs = queue.Queue()
    data_lock = threading.RLock()
    worker_lock = threading.Lock()
    log_lock = threading.Lock()

    def write(text):
        try:
            with log_lock:
                with open(log_path, "a", encoding="utf-8") as fh:
                    fh.write("[{}] {}\n".format(
                        datetime.datetime.now().isoformat(timespec="milliseconds"), text))
        except Exception:
            ctx.log_exc("npc profile: write failed")

    # ボタンは出さないので `mark` は要らない。`schedule` と例外の握りだけ借りる。
    screen = ui.Screen(ctx, write, tag="npc profile")

    # ------------------------------------------------------------ 世界と人物
    def world_key(app):
        """世界を見分ける名前。取れなければ '_'（1世界しか使わない前提に落ちる）。"""
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

    def npc_id_of(app, character_instance):
        """相手の id。**`world.characters` の鍵から引く。`.id` には頼らない。**

        `world.characters` が id で引ける辞書であることは実機で確かめてある
        （`306_` の `character_of` がこの形で名前を引けている・`out/companion.log`）。
        一方 `character_instance.id` が在るかは実測できていない ― 無ければ
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
        return ctx.out_path(STATE_DIRNAME, safe_world_filename(key))

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
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
            except Exception:
                data = {}
            bucket = data if isinstance(data, dict) else {}
            cache["buckets"][key] = bucket
            return bucket

    def save_bucket(key, bucket):
        with data_lock:
            cache["buckets"][key] = bucket
            path = state_path_for(key)
            try:
                with open(path, "w", encoding="utf-8") as fh:
                    json.dump(bucket, fh, ensure_ascii=False, indent=1)
                return True
            except Exception:
                ctx.log_exc("npc profile: cannot write {}".format(path))
                return False

    def bucket_of(app):
        """この世界の控え `{npc_id: レコード}`。

        世界の見分けは名前で行っている（`306_` と同じ制約）。**名前は外部の
        データ改変ツールで書き換えられる**ので、書き換えられた世界では控えが
        行方不明になる。安定した id は実測できていないため名前のままにして
        あるが、そうなったことが分かるように1度だけ残す。
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

    def profile_for(key, npc_id, npc_name):
        """世界名と人物 id だけで引く。ワーカーからゲームオブジェクトを触らない。"""
        with data_lock:
            bucket = load_bucket(key)
            record = bucket.get(str(npc_id))
            if not isinstance(record, dict):
                return ""
            profile = record.get("profile")
            if isinstance(profile, str) and profile.strip():
                return profile.strip()
            profile = flatten_slots(record.get("slots"))
            if not profile:
                return ""
            record["profile"] = profile
            if save_bucket(key, bucket):
                write("migrated: {!r} ({}) slots -> profile ({} chars)".format(
                    npc_name, npc_id, len(profile)))
            return profile

    def profile_of(app, npc_id):
        """MOD 固有プロフィール。旧 `slots` は初回に自動移行する。"""
        key = world_key(app)
        if not load_bucket(key):
            bucket_of(app)  # 世界名不一致の診断だけ残す
            return ""
        return profile_for(key, npc_id, name_of(app, npc_id))

    def update_profile(key, npc_id, npc_name, profile):
        """更新後のプロフィール全文を保存する。空なら既存を維持する。"""
        if not profile:
            return
        with data_lock:
            bucket = load_bucket(key)
            record = bucket.get(str(npc_id))
            if not isinstance(record, dict):
                record = {}
                bucket[str(npc_id)] = record
            old = record.get("profile")
            if isinstance(old, str) and old.strip() == profile:
                return
            record["name"] = npc_name
            record["updated"] = datetime.datetime.now().isoformat(timespec="seconds")
            record["profile"] = profile
            if save_bucket(key, bucket):
                write("updated: {!r} ({}) {} -> {} chars".format(
                    npc_name, npc_id,
                    len(old) if isinstance(old, str) else 0, len(profile)))

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
        profile = profile_of(app, npc_id)
        if not profile:
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
        clone.profile = (base.rstrip() + "\n\n" + PROFILE_HEADING + "\n" + profile
                         if base.strip() else PROFILE_HEADING + "\n" + profile)
        note_inject("{}: {!r} ({}) +{} chars into profile".format(
            label, name_of(app, npc_id), npc_id, len(profile)))
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
    # ゲームはスロットによって推論モジュールが違う。ローカルは llama_cpp、
    # 外部 API は any_server。片方しか sys.modules に載らない（`306_` と同じ）。
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
            # 出力上限は INJECT_CHARS に比例。日本語は1字≒1〜2tok なので ×3。
            result = send(manager_name, messages, max_tokens=INJECT_CHARS * 3)
        except Exception:
            ctx.log_exc("npc profile: {} failed via {}".format(
                manager_name, module_name))
            return None
        write("{}: {:.1f}s via {} -> {!r}".format(
            manager_name, time.monotonic() - started, module_name,
            _text(result, 300)))
        return result

    def read_profile(result):
        """更新後のプロフィール全文。変更なしの応答なら空を返す。"""
        if not isinstance(result, str):
            return ""
        body = result.strip()
        if not body:
            return ""
        plain = body.strip(" 　。()（）「」[]【】").lower()
        if plain in EMPTY_WORDS:
            return ""
        if (("新たに判明" in body or "新しく分かった" in body)
                and any(word in body for word in ("無い", "ない", "ありません",
                                                   "存在しません", "見当たりません"))):
            return ""
        if body.startswith("```") and body.endswith("```"):
            body = body[3:-3].strip()
            if body.startswith(("text\n", "markdown\n")):
                body = body.split("\n", 1)[1].strip()
        # 長さは抽出LLMの要約に任せる。ここで切ると文の途中で壊れる。
        return body

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
            "game_profile": _text(getattr(npc, "profile", ""), 400),
            "personality": _text(getattr(npc, "personality", ""), 300),
            "job": _text(getattr(npc, "job", ""), 60),
            "transcript": transcript,
        }

    def build_messages(snapshot, known):
        instruction = (
            "あなたは人物プロフィールの記録係だ。現在の追加プロフィールと"
            "新しい会話を統合し、更新後の追加プロフィール全文を作れ。\n\n"
            "【出力の決まり】\n"
            "- 更新後のプロフィール本文だけを書く。前置き・説明・見出しは要らない\n"
            "- 固定の分類は使わず、継続的な性格、価値観、嗜好、経歴、関係、"
            "目標、秘密、約束を自然な人物像として簡潔に統合する\n"
            "- 会話に出ていない事を推測で補ってはならない\n"
            "- 重複はまとめ、既存内容と新しい会話が矛盾するときは新しい会話を優先する\n"
            "- 書き足すのではなく要約して統合し、全体を{chars}文字以内に収める\n"
            "- 人物像に加える内容が無ければ「{no_change}」だけを出力する"
        ).format(chars=INJECT_CHARS, no_change=NO_CHANGE)
        context = (
            "【{npc_name}の素性（ゲームの記録）】\n"
            "- プロフィール: {profile}\n- 人格: {personality}\n- 役割: {job}\n\n"
            "【現在の追加プロフィール】\n{known}\n\n"
            "【新しい会話】\n{transcript}"
        ).format(npc_name=snapshot["npc_name"],
                 profile=snapshot["game_profile"],
                 personality=snapshot["personality"],
                 job=snapshot["job"],
                 known=known or "（まだ記録が無い）",
                 transcript=snapshot["transcript"])
        return [
            {"role": "user", "content": instruction + "\n\n" + context},
            {"role": "user", "content": "<行動: 追加プロフィールを更新する>"},
        ]

    def extract(snapshot):
        known = profile_for(snapshot["world"], snapshot["npc_id"],
                            snapshot["npc_name"])
        profile = read_profile(ask(
            MANAGER_EXTRACT, build_messages(snapshot, known)))
        if not profile:
            return
        update_profile(snapshot["world"], snapshot["npc_id"],
                       snapshot["npc_name"], profile)

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
