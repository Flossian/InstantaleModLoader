# -*- coding: utf-8 -*-
"""NPC同士の認知・関係記憶。311は変更しない。

v7: 応答の検証を normalize_result 1本にまとめ、構造化・非構造化の両経路が同じ道を通る。
残す事実の件数はプロンプトも設定（FACT_LOG_LIMIT）から出す。
構造化経路を一度使えなかったproviderでは、以後その経路を試さない
（試すたびに EXTRACT_TIMEOUT を2本ぶん待つため）。
311のstateは mtime を見て、動いたときだけ読み直す。
`;` 連結と1行複文をほどき、他のMODと同じ書き方に揃えた。

v6: 311と共有UI部品に合わせ、会話参加者だけをその場で読み取る。
全NPCの character_value は捕捉・保持しない。
"""
import copy
import datetime
import json
import os
import queue
import sys
import threading
import typing

from instantale_modloader import frames, llm, ui
from instantale_modloader.state import world_filename, world_key

# ---- 設定（既定値は mod.json の "settings" と一致させること。
#      `tools/check_mods.py` が AST で突き合わせる）------------------------
CONVERSATION_TURNS = 10       # 関係抽出に載せる直近のやり取りの数
RELATION_CHARS = 600          # NPC→NPC関係記録の目標長（保存・注入では切らない）
FACT_LOG_LIMIT = 10           # 方向ごとに残す事実の件数（0 で記録しない）
INJECT_PARTY_PROFILE = True   # 会話相手へ同行NPCの人物情報も見せるか
MAX_PARTICIPANTS = 6          # 1回の関係抽出で扱うNPCの上限

LOG_BASENAME = "npc_social_memory.log"

# 世界ごとの控えを置くフォルダ（`ctx.state_path` の下）。
# ファイル名は `instantale_modloader.state.world_filename` が作る。
# ここに同じ規則を写さないこと（TECH.md §3.2.3）。
STATE_DIRNAME = "npc_social_memory"

# 311 の控え。**読むだけ**で、こちらからは書かない。
PROFILE_STATE_DIRNAME = "npc_profiles"

# 会話参加者から読むゲーム側の項目と、その長さの上限。
CHARACTER_FIELDS = ("profile", "personality", "job")
CHARACTER_FIELD_CHARS = 400
CHARACTER_TOTAL_CHARS = 1000

# 311と同じく、抽出へ差し戻す既知の事実は短く再提示する。
FACT_RECALL = 10
FACT_RECALL_CHARS = 800

# 311 から借りてくる本文の上限。
PROFILE_CHARS = 600
ABOUT_PLAYER_CHARS = 600

# 会話へ注入する1項目ぶんの上限と、書き起こし1行の上限。
INJECT_FIELD_CHARS = 500
TRANSCRIPT_LINE_CHARS = 400

# 自前の `manager_name`。
# これを付けると自分のプロンプトも
# `output_data/<世界>/<PC>/<manager_name>/N.json` に残る（GAME.md §2.12）。
MANAGER_EXTRACT = "mod_npc_social_memory_extract"

# 抽出に渡す制限時間（秒）。必ず渡す。
# 抽出は1本のワーカーで直列に回しているので、1回返らないと以後が全部止まる。
EXTRACT_TIMEOUT = 120

# 抽出に載せる書き起こしの長さ（長すぎると要点が薄まる）。
CONVERSATION_CHARS = 2600

# 待ち行列へ積んだままにする仕事の上限。
# 溢れたら古い方を捨てる（新しい会話ほど関係に効くため）。
MAX_PENDING = 8

# ワーカーと控えの置き場所。
# **`apply()` の中で作ってはいけない**（TECH.md §3.4）。
# `apply()` は再注入と遅延当て直しで何度も走り、そのたびに worker が None に戻る。
# `sys` に置けば世代をまたいで同じ1組を共有できる。
STORE_ATTR = "__instantale_npc_social_memory_store__"

HEADING = "【現在この場に同行している人物】"

# 抽出LLMへ渡す JSON が読めなかったと見なす語。
FALSE_WORDS = ("false", "no", "0", "", "なし", "変更なし")


def _field(record, key):
    """控えの1項目を、前後の空白を落とした文字列で返す。無ければ空文字。"""
    value = record.get(key) if isinstance(record, dict) else None
    if isinstance(value, str) and value.strip():
        return value.strip()
    return ""


def _truthy(value):
    """`changed` の真偽。真偽値でも文字列でも読む（型を決めつけない）。"""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() not in FALSE_WORDS
    return bool(value)


def _strip_fence(text):
    """```json … ``` で包まれていたら中身だけにする。"""
    text = text.strip()
    if not text.startswith("```"):
        return text
    text = text[3:]
    end = text.rfind("```")
    if end >= 0:
        text = text[:end]
    text = text.strip()
    for tag in ("json\n", "JSON\n", "text\n", "markdown\n"):
        if text.startswith(tag):
            return text[len(tag):].strip()
    return text


def normalize_result(data, allowed_ids):
    """応答を403内部形式へ揃える。ID検証・自己参照除外・重複方向除外はここだけで行う。

    構造化経路と非構造化経路の両方がここへ来る。
    2箇所に同じ検証を書くと、片方だけ直したときに黙ってすり抜ける。
    """
    if not isinstance(data, dict):
        return None
    if not _truthy(data.get("changed", True)):
        return []
    rows = data.get("relations")
    if not isinstance(rows, list):
        return None

    allowed = {str(x) for x in allowed_ids}
    out = []
    seen = set()
    for row in rows:
        # nested create_model は provider により model のまま返る場合もある。
        row = llm.as_dict(row)
        if not isinstance(row, dict):
            continue
        observer = str(row.get("observer_id", "")).strip()
        target = str(row.get("target_id", "")).strip()
        if observer not in allowed or target not in allowed:
            continue
        if observer == target or (observer, target) in seen:
            continue
        relationship = row.get("relationship")
        if not isinstance(relationship, str) or not relationship.strip():
            continue
        raw_facts = row.get("new_facts")
        if not isinstance(raw_facts, list):
            raw_facts = []
        facts = [x.strip() for x in raw_facts if isinstance(x, str) and x.strip()]
        out.append((observer, target, relationship.strip(), facts))
        seen.add((observer, target))
    return out


def parse_result(result, allowed_ids):
    """非構造化応答からJSONを1つ取り出し、`normalize_result` へ渡す。"""
    if not isinstance(result, str):
        return None
    body = _strip_fence(result)
    start, end = body.find("{"), body.rfind("}")
    if not (0 <= start < end):
        return None
    try:
        data = json.loads(body[start:end + 1])
    except Exception:
        return None
    return normalize_result(data, allowed_ids)


def apply(ctx):
    state_dir = ctx.state_path(STATE_DIRNAME)
    write = ctx.logger(LOG_BASENAME)
    screen = ui.Screen(ctx, write, tag="npc social memory")

    store = getattr(sys, STORE_ATTR, None)
    if not isinstance(store, dict):
        store = {
            "cache": {},
            "jobs": queue.Queue(),
            "data_lock": threading.RLock(),
            "worker_lock": threading.Lock(),
            "worker": None,
            "last_inject": None,
            "last_skip": None,
        }
        setattr(sys, STORE_ATTR, store)

    # 既に在る store（注入し直し）にも、後から足した棚を用意する。
    store.setdefault("profile_311", {})      # 311 state の読み取り控え（mtime が鍵）
    store.setdefault("no_structure", False)  # 構造化経路を諦めたか

    cache = store["cache"]
    jobs = store["jobs"]
    data_lock = store["data_lock"]
    worker_lock = store["worker_lock"]

    # ------------------------------------------------------------ 403 の控え

    def path_for(key):
        return ctx.state_path(STATE_DIRNAME, world_filename(key))

    def bounded_facts(items):
        """事実を重複除去し、各 observer->target につき FACT_LOG_LIMIT 件までにする。"""
        if not isinstance(items, list):
            return []

        out = []
        seen = set()
        for item in items:
            if isinstance(item, dict):
                text = item.get("text")
                entry = dict(item)
            else:
                text = item
                entry = {}
            if not isinstance(text, str) or not text.strip():
                continue
            text = text.strip()
            if text in seen:
                continue
            seen.add(text)
            entry["text"] = text
            out.append(entry)

        if FACT_LOG_LIMIT <= 0:
            return []
        return out[-FACT_LOG_LIMIT:]

    def normalize_bucket(bucket):
        """旧403 stateも読み込み時に311のような簡潔な形へ整える。"""
        if not isinstance(bucket, dict):
            return {}, False

        changed = False
        for observer in bucket.values():
            if not isinstance(observer, dict):
                continue
            relations = observer.get("relations")
            if not isinstance(relations, dict):
                continue
            for relation in relations.values():
                if not isinstance(relation, dict) or "facts" not in relation:
                    continue
                old = relation.get("facts")
                new = bounded_facts(old)
                if old != new:
                    relation["facts"] = new
                    changed = True
        return bucket, changed

    def load_bucket(key):
        with data_lock:
            if key not in cache:
                data = ctx.read_json(path_for(key), {})
                bucket, changed = normalize_bucket(data if isinstance(data, dict) else {})
                cache[key] = bucket
                if changed:
                    # 403自身のstateだけを移行する。311や本体データには書かない。
                    ctx.write_json(path_for(key), bucket)
            return cache[key]

    def save_bucket(key, bucket):
        with data_lock:
            cache[key] = bucket
            return ctx.write_json(path_for(key), bucket)

    # ------------------------------------------------------------ 311 の控え（読むだけ）

    def profile_bucket_311(key):
        """311 の控えを返す。311 はワーカーで書き換えるので mtime/サイズで見張る。

        1ターンに会話系フックが何本も走るので、動いていない間まで読み直さない。
        """
        path = ctx.state_path(PROFILE_STATE_DIRNAME, world_filename(key))
        try:
            stat = os.stat(path)
            stamp = (stat.st_mtime_ns, stat.st_size)
        except OSError:
            # まだ311が書いていない。控えると、後から出来たときに読めなくなる。
            return {}

        with data_lock:
            cached = store["profile_311"].get(key)
            if cached is not None and cached[0] == stamp:
                return cached[1]

        data = ctx.read_json(path, {})
        bucket = data if isinstance(data, dict) else {}
        with data_lock:
            store["profile_311"][key] = (stamp, bucket)
        return bucket

    def memory_311(key, npc_id):
        record = profile_bucket_311(key).get(str(npc_id))
        if not isinstance(record, dict):
            return {"profile": "", "about_player": ""}
        return {
            "profile": frames.short(_field(record, "profile"), PROFILE_CHARS),
            "about_player": frames.short(_field(record, "about_player"),
                                         ABOUT_PLAYER_CHARS),
        }

    # ------------------------------------------------------------ 参加者を読む

    def character_text(character):
        """311と同じく、現在の会話参加者から必要な項目だけ読む。"""
        if character is None:
            return ""

        blocks = []
        total = 0
        for field in CHARACTER_FIELDS:
            value = getattr(character, field, None)
            if value in (None, "", [], {}):
                continue
            text = frames.short(value, CHARACTER_FIELD_CHARS)
            if not text:
                continue
            line = "{}: {}".format(field, text)
            remain = CHARACTER_TOTAL_CHARS - total
            if remain <= 0:
                break
            if len(line) > remain:
                # 端数が短すぎると読めない断片になるだけなので、そこで打ち切る。
                if remain > 80:
                    blocks.append(line[:remain])
                break
            blocks.append(line)
            total += len(line) + 1
        return "\n".join(blocks)

    def npc_id_of(app, npc):
        """現在の会話相手を共有UI部品で特定する。全NPC走査はしない。"""
        current = getattr(app, "in_conversation", None)
        if current is not None and ui.character_of(app, current) is npc:
            return str(current)
        # 会話関数の引数が浅い複製でも、会話相手IDを正本にする。
        if current is not None and ui.character_of(app, current) is not None:
            return str(current)
        for candidate in ui.party_member_ids(app):
            if ui.character_of(app, candidate) is npc:
                return str(candidate)
        return ""

    def participant_ids(app, conversation_id):
        ids = [str(conversation_id)] if conversation_id else []
        for member_id in ui.party_member_ids(app):
            member_id = str(member_id)
            if member_id and member_id not in ids:
                ids.append(member_id)
        return ids[:max(2, int(MAX_PARTICIPANTS))]

    def people_of(app, ids):
        key = world_key(app)
        out = []
        for npc_id in ids:
            character = ui.character_of(app, npc_id)
            if character is None:
                continue
            memory = memory_311(key, npc_id)
            out.append({
                "id": str(npc_id),
                "name": ui.character_name(app, npc_id, fallback="その人物"),
                "character": character_text(character),
                "memory_profile": memory["profile"],
                "about_player": memory["about_player"],
            })
        return out

    def player_name_of(app):
        player = getattr(app, "player", None)
        for attr in ("name", "character_name", "player_name"):
            value = getattr(player, attr, None) if player is not None else None
            if isinstance(value, str) and value.strip():
                return value.strip()
        for attr in ("player_name", "character_name"):
            value = getattr(app, attr, None)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return "プレイヤーキャラクター"

    # ------------------------------------------------------------ 関係の読み書き

    def relation_record(key, observer_id, target_id):
        observer = load_bucket(key).get(str(observer_id), {})
        relations = observer.get("relations", {}) if isinstance(observer, dict) else {}
        record = relations.get(str(target_id), {}) if isinstance(relations, dict) else {}
        return record if isinstance(record, dict) else {}

    def recent_facts(record):
        """抽出へ差し戻す既知の事実を古い順で返す。件数と文字数の両方で頭打ちにする。"""
        log = record.get("facts") if isinstance(record, dict) else None
        if not isinstance(log, list):
            return []

        texts = []
        total = 0
        for item in reversed(bounded_facts(log)):
            text = item.get("text") if isinstance(item, dict) else item
            if not isinstance(text, str) or not text.strip():
                continue
            text = text.strip()
            total += len(text) + 2
            if len(texts) >= FACT_RECALL or total > FACT_RECALL_CHARS:
                break
            texts.append(text)
        return list(reversed(texts))

    def update_relation(key, observer_id, observer_name,
                        target_id, target_name, summary, new_facts):
        with data_lock:
            bucket = load_bucket(key)

            observer = bucket.get(str(observer_id))
            if not isinstance(observer, dict):
                observer = {"name": observer_name, "relations": {}}
                bucket[str(observer_id)] = observer
            observer["name"] = observer_name

            relations = observer.get("relations")
            if not isinstance(relations, dict):
                relations = {}
                observer["relations"] = relations

            record = relations.get(str(target_id))
            if not isinstance(record, dict):
                record = {}
                relations[str(target_id)] = record

            old = _field(record, "relationship")
            record["name"] = target_name
            old_facts = bounded_facts(record.get("facts"))
            known = {x.get("text") for x in old_facts if isinstance(x, dict)}

            fresh = []
            for fact in new_facts or []:
                if not isinstance(fact, str) or not fact.strip():
                    continue
                fact = fact.strip()
                if fact not in known:
                    known.add(fact)
                    fresh.append(fact)

            record["updated"] = datetime.datetime.now().isoformat(timespec="seconds")
            record["relationship"] = summary
            if FACT_LOG_LIMIT > 0:
                log = old_facts   # 同じlistを伸ばす。下の changed の比較もこれを見る
                stamp = datetime.datetime.now().isoformat(timespec="seconds")
                for fact in fresh:
                    log.append({"at": stamp, "text": fact})
                record["facts"] = bounded_facts(log)
            else:
                record.pop("facts", None)

            changed = (old != summary
                       or old_facts != record.get("facts", [])
                       or bool(fresh))
            if not changed:
                return
            if save_bucket(key, bucket):
                write("updated {} -> {}: {} -> {} chars, +{} facts".format(
                    observer_name, target_name, len(old), len(summary), len(fresh)))

    # ------------------------------------------------------------ 会話への注入

    def note_inject(message):
        """同じ内容を続けて書かない（1ターンに会話系フックが何本も走るため）。"""
        if store["last_inject"] != message:
            store["last_inject"] = message
            write(message)

    def note_skip(message):
        if store["last_skip"] != message:
            store["last_skip"] = message
            write("extract skipped: " + message)

    def social_block(app, observer_id):
        key = world_key(app)
        blocks = []
        for target_id in ui.party_member_ids(app):
            target_id = str(target_id)
            if target_id == str(observer_id):
                continue
            character = ui.character_of(app, target_id)
            if character is None:
                continue

            name = ui.character_name(app, target_id, fallback="同行者")
            lines = ["- {} (id={})".format(name, target_id)]
            if INJECT_PARTY_PROFILE:
                memory = memory_311(key, target_id)
                raw = character_text(character)
                if raw:
                    lines.append("  ゲーム側の基本情報（短縮）: "
                                 + frames.short(raw, INJECT_FIELD_CHARS))
                if memory["profile"]:
                    lines.append("  311の人物像（短縮）: "
                                 + frames.short(memory["profile"], INJECT_FIELD_CHARS))
                if memory["about_player"]:
                    lines.append("  {}から見た{}: {}".format(
                        name, player_name_of(app),
                        frames.short(memory["about_player"], INJECT_FIELD_CHARS)))

            relationship = _field(relation_record(key, observer_id, target_id),
                                  "relationship")
            lines.append("  403が記録した関係（短縮）: " + (
                frames.short(relationship, RELATION_CHARS) if relationship else
                "（このMODにはまだ確定した記録がない。初対面か既知かを捏造せず、"
                "現在の会話・ゲーム側の記録から判断すること）"))
            blocks.append("\n".join(lines))

        if not blocks:
            return ""
        return (HEADING + "\n"
                "以下の人物は現在プレイヤーと同行し、この会話の場にいる。"
                "あなたが演じるのは会話相手であるあなた自身だけで、"
                "同行者の台詞を代行してはならない。"
                "ただし存在・発言・既知の関係は認識してよい。\n"
                + "\n".join(blocks))

    def with_context(label, args, kwargs):
        """会話関数に渡すNPCを、社会記録を足した**浅い複製**に差し替える。

        本体のNPCには書かない。書くと世界の人物像そのものが伸び続ける。
        """
        npc = kwargs.get("character_instance")
        if npc is None and len(args) >= 4:
            npc = args[3]
        app = ui.find_app()
        if npc is None or app is None:
            return args, kwargs

        observer_id = npc_id_of(app, npc)
        if not observer_id:
            return args, kwargs

        block = social_block(app, observer_id)
        if not block:
            note_inject(label + ": no party social context")
            return args, kwargs

        try:
            clone = copy.copy(npc)
        except Exception:
            ctx.log_exc("npc social memory: cannot copy NPC")
            return args, kwargs

        base = getattr(npc, "profile", "")
        if not isinstance(base, str):
            return args, kwargs
        clone.profile = (base.rstrip() + "\n\n" + block) if base.strip() else block
        note_inject("{}: {} +{} social chars".format(
            label, ui.character_name(app, observer_id), len(block)))

        if "character_instance" in kwargs:
            new_kwargs = dict(kwargs)
            new_kwargs["character_instance"] = clone
            return args, new_kwargs
        new_args = list(args)
        new_args[3] = clone
        return tuple(new_args), kwargs

    def inject(orig, label, args, kwargs):
        try:
            args, kwargs = with_context(label, args, kwargs)
        except Exception:
            ctx.log_exc("npc social memory: injection failed")
        return orig(*args, **kwargs)

    # ------------------------------------------------------------ 抽出の材料

    def transcribe(app, conversation_id):
        """直近のやり取りを1本の文字列にする。ゲーム自身の整形をまず使う。"""
        history = getattr(app, "current_conversation_history", None)
        if not isinstance(history, list) or not history:
            return ""

        recent = history[-CONVERSATION_TURNS:]
        npc = ui.character_of(app, conversation_id)
        module = sys.modules.get("scripts.llm.context_manager")
        to_text = getattr(module, "conversation_history_to_text", None) if module else None
        if to_text is not None and npc is not None:
            try:
                text = to_text(recent, getattr(app, "player", None), npc)
                if isinstance(text, str) and text.strip():
                    return text.strip()[-CONVERSATION_CHARS:]
            except Exception as error:
                write("conversation_history_to_text failed: {}: {}".format(
                    type(error).__name__, error))

        lines = ["{}: {}".format(item.get("role", "?"),
                                 frames.short(item.get("content"), TRANSCRIPT_LINE_CHARS))
                 for item in recent if isinstance(item, dict)]
        return "\n".join(lines)[-CONVERSATION_CHARS:]

    def snapshot(app, conversation_id):
        """抽出に必要なものだけを、その場で写し取る（ワーカーは app を触らない）。"""
        ids = participant_ids(app, conversation_id)
        people = people_of(app, ids)
        if len(people) < 2:
            note_skip("fewer than two NPC participants: {}".format(ids))
            return None

        transcript = transcribe(app, conversation_id)
        if not transcript:
            note_skip("conversation transcript is empty")
            return None

        key = world_key(app)
        existing = {}
        for observer in people:
            for target in people:
                if observer["id"] == target["id"]:
                    continue
                record = relation_record(key, observer["id"], target["id"])
                existing[(observer["id"], target["id"])] = (
                    _field(record, "relationship"), recent_facts(record))
        return {"world": key, "people": people,
                "transcript": transcript, "existing": existing}

    def build_messages(snap):
        people = snap["people"]
        ids = [person["id"] for person in people]
        app = ui.find_app()
        player_name = player_name_of(app) if app is not None else "プレイヤーキャラクター"

        participant_index = "\n".join(
            "- id={} / 名前={}".format(person["id"], person["name"])
            for person in people
        )

        # 人ごとにまとめて並べる（読む側が1人ぶんを続けて追えるように）。
        sections = []
        for person in people:
            blocks = [
                "【{}の基本情報】\n{}".format(
                    person["name"], person["character"] or "（取得できず）"),
                "【311:別MODにより、形成された{}の現在情報】\n{}".format(
                    person["name"], person["memory_profile"] or "（まだ記録なし）"),
                "【311: 別MODにより、形成された{}から見た{}】\n{}".format(
                    person["name"], player_name,
                    person["about_player"] or "（まだ記録なし）"),
            ]
            for target in people:
                if target["id"] == person["id"]:
                    continue
                relationship, facts = snap["existing"].get(
                    (person["id"], target["id"]), ("", []))
                body = relationship or "（まだ記録なし）"
                if facts:
                    body += "\n既知facts: " + " / ".join(facts)
                blocks.append(
                    "【403: 過去のやり取りより追加された、{}から見た{}の人物像】\n{}".format(
                        person["name"], target["name"], body))
            sections.append("\n\n".join(blocks))

        # 残す件数は設定（FACT_LOG_LIMIT）で決まる。プロンプトに固定値を書くと、
        # 設定を動かした瞬間に「頼んだ件数」と「残る件数」がずれる。
        if FACT_LOG_LIMIT > 0:
            fact_rule = ("- new_factsは今回新しく確定し今後も意味を持つNPC間の事実だけ。"
                         "既知factsの言い換えは禁止。1方向につき最大{}件までとし、"
                         "過去の事実はrelationshipの要約へ圧縮する。".format(FACT_LOG_LIMIT))
        else:
            fact_rule = ("- new_factsは保存しない設定なので、常に空配列にする。"
                         "確定した事実はrelationshipの要約へ織り込む。")

        prompt = """あなたはNPC同士の社会的記憶の記録係だ。
参加NPCの基本情報、311が形成した現在情報、そのNPCから見た{player_name}、
403が過去のやり取りから形成したNPC同士の人物像、そして新しい会話を材料に、
NPCが別のNPCをどう認識しているかを更新せよ。
- {player_name}についての記録は作らない。
- observer_id/target_id は参加NPCのidをそのまま使う。
- A→BとB→Aは別で、対称にしない。
- observer自身の基本情報にある性格・価値観・感情傾向（嫉妬心、執着、好奇心、怒りやすさ等）を反応判断に使う。
- 「311: 別MODにより、形成された○○から見た{player_name}」は重要な材料である。{player_name}への親愛・恋愛感情・執着・競争心等が既にあるなら、{player_name}と別NPCとのやり取りを目撃して、その別NPCへの嫉妬・警戒・好感・対抗心等が生じることはあり得る。ただし人物設定と会話内容から自然に判断し、機械的に嫉妬させない。
- 大事件だけでなく、軽い嫉妬、興味、好印象、違和感、警戒、親近感など、今後のNPC同士の振る舞いに影響し得る小さな変化も記録してよい。
- 会話に根拠のない面識・好意・敵意を捏造しない。
- 同行して同じ会話を聞いているNPCは、その場で明示された発言・事実を認知したものとしてよい。
- relationship は相手を誰として認識し、どう評価・信頼・警戒・親愛・敵視しているか等を短い現在要約として統合する。時系列のあらすじや追記ログは書かない。
- 既存記録に無い変化が無い方向はrelationsに出さない。初めて相手の存在を認知したこと自体は記録してよい。
{fact_rule}
- relationshipは各方向おおむね{chars}文字以内。
- JSONオブジェクト1つだけ返す。
出力: {{"changed":"true または false","relations":[{{"observer_id":"id","target_id":"id","relationship":"更新後全文","new_facts":["新事実"]}}]}}
changed=falseならrelations=[]。""".format(player_name=player_name,
                                          chars=RELATION_CHARS,
                                          fact_rule=fact_rule)

        content = (prompt
                   + "\n\n【参加NPC】\n" + participant_index
                   + "\n\n" + "\n\n".join(sections)
                   + "\n\n【新しい会話】\n" + snap["transcript"])
        return [{"role": "user", "content": content},
                {"role": "user", "content": "<行動: NPC同士の関係記録を更新する>"}], ids

    # ------------------------------------------------------------ 抽出

    def max_tokens_for(ids):
        return max(1200, RELATION_CHARS * max(2, len(ids)))

    def ask_structured(messages, ids):
        """最新版llm共通部品の構造化出力を優先する。使えなければNone。"""
        relation = llm.create_structure(
            ctx, "NpcSocialRelationUpdate",
            {"observer_id": (str, ...), "target_id": (str, ...),
             "relationship": (str, ...), "new_facts": (typing.List[str], ...)},
            label="npc social memory")
        if relation is None:
            return None

        structure = llm.create_structure(
            ctx, "NpcSocialMemoryUpdate",
            {"changed": (str, ...), "relations": (typing.List[relation], ...)},
            label="npc social memory")
        if structure is None:
            return None

        return llm.ask(ctx, MANAGER_EXTRACT, messages, timeout=EXTRACT_TIMEOUT,
                       structure=structure, max_tokens=max_tokens_for(ids),
                       label="npc social memory", write=write)

    def extract(snap):
        messages, ids = build_messages(snap)

        rows = None
        if not store["no_structure"]:
            structured = ask_structured(messages, ids)
            rows = normalize_result(structured, ids) if structured is not None else None
            if rows is None:
                # 一度失敗したproviderで毎回試すと、失敗のたびに
                # EXTRACT_TIMEOUT を2本ぶん待つことになる。以後は非構造化だけにする。
                store["no_structure"] = True
                write("extract: structured route unusable; "
                      "falling back to plain JSON from now on")

        if rows is None:
            # 構造化出力を使えないproviderだけ旧no-structure経路へ降りる。
            result = llm.ask(ctx, MANAGER_EXTRACT, messages, timeout=EXTRACT_TIMEOUT,
                             max_tokens=max_tokens_for(ids),
                             label="npc social memory", write=write)
            rows = parse_result(result, ids)

        if rows is None:
            write("extract: unreadable response; no change")
            return
        if not rows:
            write("extract: nothing to change")
            return

        by_id = {person["id"]: person for person in snap["people"]}
        for observer_id, target_id, relationship, facts in rows:
            if observer_id in by_id and target_id in by_id:
                update_relation(snap["world"],
                                observer_id, by_id[observer_id]["name"],
                                target_id, by_id[target_id]["name"],
                                relationship, facts)

    # ------------------------------------------------------------ ワーカー

    def worker_loop():
        while True:
            try:
                snap = jobs.get(timeout=30.0)
            except queue.Empty:
                with worker_lock:
                    if not jobs.empty():
                        continue
                    store["worker"] = None
                return

            try:
                extract(snap)
            except Exception:
                ctx.log_exc("npc social memory: background extraction failed")
            finally:
                jobs.task_done()

    def enqueue(app, conversation_id):
        snap = snapshot(app, conversation_id)
        if snap is None:
            return

        with worker_lock:
            while jobs.qsize() >= MAX_PENDING:
                try:
                    jobs.get_nowait()
                except queue.Empty:
                    break
                jobs.task_done()
                write("extract: dropped oldest pending job")

            jobs.put(snap)
            store["last_skip"] = None
            write("extract queued: participants={} character={} 311profile={} "
                  "about_player={} transcript={} chars".format(
                      [p["name"] for p in snap["people"]],
                      [len(p["character"]) for p in snap["people"]],
                      [len(p["memory_profile"]) for p in snap["people"]],
                      [len(p["about_player"]) for p in snap["people"]],
                      len(snap["transcript"])))

            worker = store.get("worker")
            if worker is not None and worker.is_alive():
                return
            worker = threading.Thread(target=worker_loop,
                                      name="instantale_mod.npc_social_memory",
                                      daemon=True)
            store["worker"] = worker
            worker.start()

    def schedule_extract(app):
        app = app or ui.find_app()
        if app is None:
            note_skip("no running app")
            return
        conversation_id = getattr(app, "in_conversation", None)
        if not isinstance(conversation_id, str) or not conversation_id:
            note_skip("in_conversation is {!r}".format(conversation_id))
            return
        if not screen.schedule(lambda: enqueue(app, conversation_id)):
            note_skip("could not schedule next-frame snapshot")

    # ================================================================ hooks

    @ctx.wrap("__main__:ConversationPhaseManager.conversation_continued",
              required=False)
    def conversation_continued(orig, self, choice_text, *args, **kwargs):
        result = orig(self, choice_text, *args, **kwargs)
        try:
            schedule_extract(getattr(self, "app", None))
        except Exception:
            ctx.log_exc("npc social memory: cannot schedule extraction")
        return result

    @ctx.wrap("__main__:ConversationInQuestPhase.conversation_continued",
              required=False)
    def conversation_continued_in_quest(orig, self, choice_text, *args, **kwargs):
        result = orig(self, choice_text, *args, **kwargs)
        try:
            schedule_extract(getattr(self, "app", None))
        except Exception:
            ctx.log_exc("npc social memory: cannot schedule extraction")
        return result

    @ctx.wrap("scripts.llm.llm_manager:conversation_facilitator", required=False)
    def facilitator(orig, *args, **kwargs):
        return inject(orig, "facilitator", args, kwargs)

    @ctx.wrap("scripts.llm.llm_manager:conversation_facilitator_after_retrieval",
              required=False)
    def facilitator_after_retrieval(orig, *args, **kwargs):
        return inject(orig, "facilitator[retrieval]", args, kwargs)

    @ctx.wrap("scripts.llm.llm_manager:conversation_facilitator_in_quest",
              required=False)
    def facilitator_in_quest(orig, *args, **kwargs):
        return inject(orig, "facilitator[quest]", args, kwargs)

    @ctx.wrap("scripts.llm.llm_manager:conversation_starter", required=False)
    def starter(orig, *args, **kwargs):
        return inject(orig, "starter", args, kwargs)

    @ctx.wrap("scripts.llm.llm_manager:conversation_starter_in_quest",
              required=False)
    def starter_in_quest(orig, *args, **kwargs):
        return inject(orig, "starter[quest]", args, kwargs)

    ctx.log("npc social memory v7: state={}/; participant-only capture; "
            "game data untouched; 311 state READ ONLY".format(state_dir))
