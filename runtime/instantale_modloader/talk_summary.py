# -*- coding: utf-8 -*-
"""会話を閉じるときのゲームの要約の頼みに、MOD の判定の項目を相乗りさせる窓口。

ゲームは会話を閉じるとき、会話の全文を要約させる（`llm_manager:conversation_resolver`
→ 同じスレッドで `send_request("conversation_resolver", message, Result{summary})`）。
その頼みには相手の人物像・プレイヤーへの今の感情・会話の全文がもう載っているので、
会話の中身から何かを判定したい MOD は、LLM を別に呼ばずにここへ項目を足せる（TECH.md §3.3.16）。
ゲームは要約が終わるまで会話を閉じきらないので、別に呼ぶとその待ちがそのぶん延びる。

乗る MOD が2本以上になると、各々が `send_request` を包んで頼みの型と本文を重ねて書き換えることになり、
足した形が失敗したときの送り直しも MOD の数だけ走る。
MOD どうしは import しない（TECH.md §3.2.3）ので、包みはここに1組だけ置く。

    apply() の中で   talk_summary.install(ctx, write)
                     talk_summary.ride(owner, ctx, begin, settle, fields)

        begin(talk)           -> 書き足す決まりの文 / None（この会話には乗らない）
        settle(talk, answer)  要約が返った後（`conversation_resolver` の `orig` の後）。
                              answer はその MOD の項目だけの辞書。答えが無ければ None
        fields                {"項目名": 型}（int / str）。項目名は MOD どうしで重ねない

`talk` は1回の要約につき1つの辞書で、乗った MOD の全部に同じものが渡る。

| 鍵 | 中身 |
|---|---|
| `app` / `character` / `messages` | ゲームの app・相手の人物・会話の全文（要約の関数の引数） |
| `npc_id` / `name` / `player` | 相手の id（文字列）・相手の名前・主人公の名前 |
| `status` | `"answered"` / `"not_sent"`（要約が送信を通らなかった） / `"no_structure"` / `"no_system"` / `"cannot_extend"` / `"retried"`（足した形が失敗し、元の頼みで送り直した） / `"unreadable"`（答えを辞書に読めなかった） |
| `detail` | `status` の補足（失敗の例外など）。無ければ空 |

| 決まり | 理由 |
|---|---|
| 決まりの文は先頭の system に、乗った順に書き足す | system を先頭にしか置けないチャットテンプレートがある |
| 頼みの型は「元の型＋乗った MOD の項目全部」の子の型1つ。同じ顔ぶれなら使い回す | 足した項目はスキーマ文と grammar の両方に出る（`send_request` の中で型から作られる） |
| 足した形が失敗したら、元の頼みで1回だけ送り直す | 要約を落とさないのが先。送り直しは乗った MOD の数によらず1回 |
| ゲームへは元の型の形で返す（足した項目を抜く） | 判定を要約の記録・会話の記憶・セーブに混ぜない |
| 項目名が先に乗った MOD と重なった MOD は、その回は乗せない（`errors` に残す） | 答えの持ち主が決まらない |
| 登録は持ち主ごとに1つ。登録した ctx が用済み（`ctx.superseded()`）なら数えない。`begin` / `settle` が投げたら、その MOD だけ降りる | `talk_affinity`（§3.3.8）と同じ |

包みは1つの世代につき1組（`arrivals.install` と同じ形）。乗る MOD は必ず `install` も呼ぶこと。
置き場は `sys` の属性（`_instantale_talk_summary`）。注入し直しをまたいで残る。
"""
import json
import re
import sys
import threading

from . import llm, ui

RESOLVER_TARGET = "scripts.llm.llm_manager:conversation_resolver"
SEND_TARGET = "scripts.llm.llm_manager:send_request"
RESOLVER_MANAGER = "conversation_resolver"     # 要約が send_request に渡す manager_name
MOD_NPC_PREFIX = "mod:"
ACTION_PATTERN = re.compile(r"^[<＜]\s*行動\s*[:：]\s*(.*?)\s*[>＞]?\s*$", re.S)
#: 会話の開け閉めだけの行動（実際の要約の記録の user の行に出た印）。
TRIVIAL_ACTIONS = ("話しかける", "会話を終了する", "取引を終了する")

STORE_ATTR = "_instantale_talk_summary"
_GATE_ATTR = "_instantale_talk_summary_gate"


def _store():
    store = getattr(sys, STORE_ATTR, None)
    if not isinstance(store, dict):
        store = {"riders": {}, "errors": []}
        setattr(sys, STORE_ATTR, store)
    return store


def _error(text):
    errors = _store()["errors"]
    errors.append(text)
    del errors[:-20]


# ============================================================ 登録
def ride(owner, ctx, begin, settle, fields):
    """`owner` を要約に乗せる。同じ持ち主の登録は差し替える。"""
    _store()["riders"][str(owner)] = {"ctx": ctx, "begin": begin, "settle": settle,
                                      "fields": dict(fields or {})}


def withdraw(owner):
    """登録を下げる。"""
    _store()["riders"].pop(str(owner), None)


def _alive(ctx):
    superseded = getattr(ctx, "superseded", None)
    if not callable(superseded):
        return True
    try:
        return not superseded()
    except Exception:
        return True


def owners():
    """いま数えている登録の持ち主（乗る順）。ログ用。"""
    return [owner for owner, rider in _store()["riders"].items() if _alive(rider["ctx"])]


def reset():
    """全部忘れる（検査用）。"""
    setattr(sys, STORE_ATTR, {"riders": {}, "errors": []})
    if hasattr(sys, _GATE_ATTR):
        delattr(sys, _GATE_ATTR)


# ============================================================ 部品（ゲームに触らない）
def action_of(text):
    """行動の印（<行動: …>）の中身。印でなければ None。"""
    found = ACTION_PATTERN.match(text)
    return found.group(1).strip() if found else None


def player_engaged(messages):
    """プレイヤーが何か言ったか、開け閉め以外の行動（贈り物・売り買い・別れなど）をしたか。"""
    for turn in messages or []:
        if not isinstance(turn, dict) or turn.get("role") != "user":
            continue
        text = str(turn.get("content") or "").strip()
        if not text:
            continue
        action = action_of(text)
        if action is None or action not in TRIVIAL_ACTIONS:
            return True
    return False


def with_instruction(message, text):
    """先頭の system の本文に `text` を書き足した写しを返す。system が無ければ None。

    ゲームが持っている dict は書き換えない（浅い写しを差し替える）。
    """
    if not isinstance(message, list):
        return None
    for index, turn in enumerate(message):
        if isinstance(turn, dict) and turn.get("role") == "system" \
                and isinstance(turn.get("content"), str):
            copied = list(message)
            copied[index] = dict(turn, content=turn["content"].rstrip() + "\n\n" + text)
            return copied
    return None


def restore(raw, structure, added):
    """足した型で返ってきた答えを、元の型の形に戻す（`added` の項目を抜く）。

    返る形（pydantic のモデル・辞書・JSON 文字列）はプロバイダで変わるので（`llm.as_dict`）、
    来た形に合わせて戻す。戻せなければそのまま返す（足した型は元の型の子なので、ゲームはそのまま読める）。
    """
    added = set(added)

    def strip(data):
        return {key: value for key, value in data.items() if key not in added}

    if isinstance(raw, dict):
        return strip(raw)
    if isinstance(raw, str):
        data = llm.as_dict(raw)
        return json.dumps(strip(data), ensure_ascii=False) if data is not None else raw
    dump = getattr(raw, "model_dump", None)
    if callable(dump):
        data = strip(dump())
        validate = getattr(structure, "model_validate", None)
        return validate(data) if callable(validate) else structure(**data)
    return raw


def npc_id_of(app, character):
    """相手の id（文字列）。読めなければ空。"""
    value = getattr(character, "id", None)
    if value is not None and not isinstance(value, bool):
        return str(value)
    current = getattr(app, "in_conversation", None)
    return str(current) if current not in (None, False, True) else ""


def player_name(app):
    name = getattr(getattr(app, "player", None), "name", None)
    return name.strip() if isinstance(name, str) and name.strip() else "プレイヤー"


# ============================================================ 包み
def installed():
    """包みの状態。`{"generation", "targets"}` か、立っていなければ None。"""
    done = getattr(sys, _GATE_ATTR, None)
    return done if isinstance(done, dict) else None


def install(ctx, write=None):
    """要約の関数と送信の境目を包む。包んだ対象の名前を返す。

    乗る MOD が `apply()` の中で呼ぶ。何本の MOD が呼んでも1つの世代につき1組。
    """
    generation = getattr(ctx, "generation", None)
    done = installed()
    if done is not None and (done.get("ctx") is ctx or (
            generation is not None and done.get("generation") == generation)):
        return list(done.get("targets") or [])
    say = write or (lambda text: None)
    #: 要約の関数から送信の境目へ「この要約に乗る MOD」を渡す。同じスレッドで降りてくる。
    pending = threading.local()
    #: (元の型, 足した項目の並び) -> 足した型。要約の型は毎回同じなので使い回す。
    extended_types = {}

    def extended(structure, fields):
        key = (id(structure), tuple(fields))
        known = extended_types.get(key)
        if known is not None and known[0] is structure:
            return known[1]
        module = llm.manager()
        factory = getattr(module, "create_model", None) if module is not None else None
        if not callable(factory):
            return None
        child = factory(getattr(structure, "__name__", "Result"), __base__=structure,
                        **{name: (kind, ...) for name, kind in fields.items()})
        extended_types[key] = (structure, child)
        return child

    def prepare(job, args, kwargs):
        """足した形の `(args, kwargs, 元の型)`。足せなければ None（元のまま送る）。"""
        talk = job["talk"]
        structure = args[2] if len(args) > 2 else kwargs.get("structure")
        if structure is None:
            talk["status"] = "no_structure"
            return None
        message = args[1] if len(args) > 1 else kwargs.get("message")
        text = "\n\n".join(instruction for _owner, _rider, instruction in job["riding"])
        new_message = with_instruction(message, text)
        if new_message is None:
            talk["status"] = "no_system"
            return None
        child = extended(structure, job["fields"])
        if child is None:
            talk["status"] = "cannot_extend"
            return None
        args = list(args)
        kwargs = dict(kwargs)
        if len(args) > 1:
            args[1] = new_message
        else:
            kwargs["message"] = new_message
        if len(args) > 2:
            args[2] = child
        else:
            kwargs["structure"] = child
        return tuple(args), kwargs, structure

    def install_send(target):
        @ctx.wrap(target, required=False)
        def send_request(orig, *args, **kwargs):
            job = getattr(pending, "job", None)
            manager_name = args[0] if args else kwargs.get("manager_name")
            if job is None or job["sent"] or manager_name != RESOLVER_MANAGER:
                return orig(*args, **kwargs)
            job["sent"] = True                  # 要約1回に1度だけ
            talk = job["talk"]
            try:
                prepared = prepare(job, args, kwargs)
            except Exception as exc:
                ctx.log_exc("talk summary: cannot extend the summary request")
                talk["status"], talk["detail"] = "cannot_extend", repr(exc)
                prepared = None
            if prepared is None:
                return orig(*args, **kwargs)
            new_args, new_kwargs, structure = prepared
            try:
                raw = orig(*new_args, **new_kwargs)
            except Exception as exc:
                talk["status"] = "retried"
                talk["detail"] = "{}: {}".format(type(exc).__name__, exc)
                say("talk summary: retry {} ({}): the extended summary failed ({}); "
                    "sending it as is".format(talk["name"], ", ".join(
                        owner for owner, _rider, _text in job["riding"]), talk["detail"]))
                return orig(*args, **kwargs)
            try:
                job["answer"] = llm.as_dict(raw)
                talk["status"] = "answered" if isinstance(job["answer"], dict) else "unreadable"
                return restore(raw, structure, job["fields"])
            except Exception as exc:
                ctx.log_exc("talk summary: cannot restore the summary answer")
                talk["status"], talk["detail"] = "unreadable", repr(exc)
                return raw

    llm.watch_aliases(ctx, [SEND_TARGET], install_send, label="talk summary")

    def gather(talk):
        """この要約に乗る MOD。`(job, ...)` を作る。誰も乗らなければ None。"""
        riding, fields = [], {}
        for owner, rider in list(_store()["riders"].items()):
            if not _alive(rider["ctx"]):
                continue
            clash = [name for name in rider["fields"] if name in fields]
            if clash:
                _error("{}: fields {} are already taken".format(owner, clash))
                continue
            try:
                instruction = rider["begin"](talk)
            except Exception:
                rider["ctx"].log_exc("talk summary: {} could not decide".format(owner))
                continue
            if not isinstance(instruction, str) or not instruction.strip():
                continue
            riding.append((owner, rider, instruction.strip()))
            fields.update(rider["fields"])
        if not riding:
            return None
        return {"talk": talk, "riding": riding, "fields": fields, "sent": False, "answer": None}

    def settle(job):
        talk, answer = job["talk"], job["answer"]
        for owner, rider, _text in job["riding"]:
            mine = None
            if talk["status"] == "answered" and isinstance(answer, dict):
                mine = {name: answer.get(name) for name in rider["fields"]}
            try:
                rider["settle"](talk, mine)
            except Exception:
                rider["ctx"].log_exc("talk summary: {} could not settle".format(owner))

    def conversation_resolver(orig, *args, **kwargs):
        """要約に乗る MOD の項目を相乗りさせる。引数は受け取ったまま `orig` へ渡し、戻り値もそのまま返す。"""
        job = None
        try:
            messages = kwargs.get("messages", args[1] if len(args) > 1 else None)
            character = kwargs.get("character_instance", args[3] if len(args) > 3 else None)
            app = ui.find_app()
            if app is not None and character is not None and isinstance(messages, list):
                npc_id = npc_id_of(app, character)
                talk = {"app": app, "character": character, "messages": messages,
                        "npc_id": npc_id, "name": getattr(character, "name", None) or npc_id,
                        "player": player_name(app), "status": "not_sent", "detail": ""}
                job = gather(talk)
        except Exception:
            ctx.log_exc("talk summary: cannot prepare the riders")
        previous = getattr(pending, "job", None)
        pending.job = job
        try:
            result = orig(*args, **kwargs)
        finally:
            pending.job = previous
        if job is not None:
            settle(job)
        return result

    ctx.wrap(RESOLVER_TARGET, required=False, safe=True)(conversation_resolver)
    setattr(sys, _GATE_ATTR, {"generation": generation, "ctx": ctx,
                              "targets": [RESOLVER_TARGET, SEND_TARGET]})
    say("talk summary: riding on {} and {}".format(RESOLVER_TARGET, SEND_TARGET))
    return [RESOLVER_TARGET, SEND_TARGET]
