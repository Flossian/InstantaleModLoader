# -*- coding: utf-8 -*-
"""会話の中身で NPC の好感度が上下する。

素のゲームの好感度（`relationship.player.affinity`）は依頼のクリアで同行者に +20 され、
会話ではほとんど動かない（会話を閉じた192回のうち1回。GAME.md §2.25.1）。
このMODは会話を閉じるたびに、その会話で相手の気持ちがどう動いたかを -3〜+3 で判定させ、
好感度へ足す。仕様と決めた経緯は DOC.md。

##### どこで聞くか（要約の頼みに相乗りする）

LLM を別に呼ばない。
ゲームが会話の終わりに回す要約（`llm_manager:conversation_resolver`）の頼みに、判定の項目を足す。
要約の頼みには相手の人物像・プレイヤーへの今の感情・会話の全文がもう載っているので、材料は足さなくてよい。
ゲームは要約が終わるまで会話を閉じきらないので（GAME.md §2.5）、別に呼ぶとその待ちがそのぶん延びた。

- 要約の関数を包んで「いまの要約はこの相手の分」とスレッドに印を付ける
- 要約は同じスレッドで `llm_manager:send_request(manager_name, message, structure, ...)` を通る
  （`213_` の記録。ローカルでもクラウドでも同じ別名）。
  そこで `structure`（要約の返却型 `Result{summary}`）を、判定の2項目を足した子の型に差し替え、
  先頭の system の頼み文に判定の決まりを書き足す。
  スキーマ文は `send_request` の中で型から作られるので（入口で4件、記録では5件）、足した項目もそこに出る
- 返ってきたら判定の2項目を抜き、元の型に戻してゲームへ返す（要約の記録とセーブに判定を混ぜない）
- 足した形で送って失敗したら、元の頼みのまま送り直す（要約を落とさないのが先）

感情の文（`affinity_text`）はゲームが会話の始まりと終わり、依頼のクリアで好感度から作り直すので
（GAME.md §2.25.1）、この MOD は好感度の数だけを書く。
書くのは要約が返った直後で、ゲームが終わりの感情の文を作り直すより前。
動いたことは本文に出さない（ナレーションは要らない）。ログにだけ残す。

##### 釣り合い

依頼のクリア1回が同行者全員に +20 なので、会話はそれより小さく動かす。

- 上がるときは1段 `GAIN_PER_STEP`（既定 2、最大 +6）、下がるときは1段 `LOSS_PER_STEP`（既定 4、最大 -12）
- 会話だけで上げられるのは `TALK_CEILING`（既定 60＝「仲間だと感じている」）まで。それより上は冒険を共にして上げる
- 他の MOD が相手ごとに上限を狭めていれば、それに従う（ローダの窓口 `talk_affinity`。TECH.md §3.3.8）
- 上がるのは同じ相手から1日1回まで（日数は宿泊・街の移動でしか進まないので、同じ街で話し続けても稼げない）。
  下がる方は毎回効く（回数の制限を付けない）
- 話しかけて閉じただけの会話は判定を足さない。
  プレイヤーが何か言ったか、決まりきった行動（`TRIVIAL_ACTIONS`）以外の行動（贈り物・売り買い・別れなど）があれば判定する
- MOD の NPC（id が `mod:` で始まる。330 の管理人・331 の主人）は対象にしない
"""

import json
import re
import sys
import threading

from instantale_modloader import frames, llm, state as loader_state, talk_affinity, ui
from instantale_modloader.npcs import npc_stores

# ---- 設定（既定値は mod.json の "settings" と一致させること。
#      `tools/check_mods.py` が AST で突き合わせる）------------------------
GAIN_PER_STEP = 2             # 上がる1段あたりの好感度
LOSS_PER_STEP = 4             # 下がる1段あたりの好感度
TALK_CEILING = 60             # 会話だけで上げられる好感度の上限
DAILY_GAIN_ONCE = True        # 同じ相手から上がるのは1日1回

# ---- 設定にしない定数 ----------------------------------------------------
LOG_BASENAME = "conversation_affinity.log"
STATE_DIRNAME = "conversation_affinity"
STORE_ATTR = "_instantale_conversation_affinity"
RESOLVER_TARGET = "scripts.llm.llm_manager:conversation_resolver"
SEND_TARGET = "scripts.llm.llm_manager:send_request"
RESOLVER_MANAGER = "conversation_resolver"     # 要約が send_request に渡す manager_name
CHANGE_FIELD = "affinity_change"
REASON_FIELD = "affinity_reason"
ADDED_FIELDS = (CHANGE_FIELD, REASON_FIELD)
STEP_LIMIT = 3                # 段の絶対値の上限
REASON_CHARS = 80
AFFINITY_FLOOR = -100         # 好感度の下限（ゲームの段は -41 以下が最下段）
MOD_NPC_PREFIX = "mod:"
ACTION_PATTERN = re.compile(r"^[<＜]\s*行動\s*[:：]\s*(.*?)\s*[>＞]?\s*$", re.S)
#: 会話の開け閉めだけの行動。これしか無い会話は判定しない（実際の記録に出た印）。
TRIVIAL_ACTIONS = ("話しかける", "会話を終了する", "取引を終了する")

INSTRUCTION = """【追加の判定】
要約とは別に、この会話で{name}の{player}への好感がどう動いたかを判定し、
affinity_change に -3〜+3 の整数で答えること。
- 0: 変わらない。用件だけのやり取りや普通の雑談はほぼ 0
- +1: 少し好感を持った / +2: はっきり好感を持った / +3: 強く心を動かされた
- -1: 少し不快だった / -2: はっきり不快だった / -3: 強い怒りや屈辱を感じた
{name}の性格・立場と、{player}に対する今の感情から判断すること。{player}がおだてたり媚びたりしただけでは上げないこと。
affinity_reason には判定の理由を{name}の立場から1文で書くこと。
summary にはこの判定のことを書かないこと。"""


def _store():
    """世代をまたぐ入れ物。プロセスに1つ（TECH.md §3.5）。"""
    store = getattr(sys, STORE_ATTR, None)
    if not isinstance(store, dict):
        store = {"worlds": None}
        setattr(sys, STORE_ATTR, store)
    return store


# ============================================================ 部品（ゲームに触らない）
def clamp_step(value):
    """LLM の答えを -3〜+3 の整数に均す。読めなければ None。"""
    if isinstance(value, bool):
        return None
    if isinstance(value, str):
        value = value.strip().lstrip("+")
    try:
        number = int(float(value))
    except (TypeError, ValueError, OverflowError):
        return None
    return max(-STEP_LIMIT, min(STEP_LIMIT, number))


def plan_change(before, step, *, gain_per_step, loss_per_step, ceiling, gained_today):
    """`(新しい好感度, 理由)` を返す。動かさないときは `(before, 理由)`。

    | 理由 | |
    |---|---|
    | `"gain"` / `"loss"` | 動かした |
    | `"zero"` | 段が 0 |
    | `"daily"` | 今日はもう上げた |
    | `"ceiling"` | 会話で上げられる上限に届いている |
    """
    if not step:
        return before, "zero"
    if step > 0:
        if gained_today:
            return before, "daily"
        if before >= ceiling:
            return before, "ceiling"
        return min(ceiling, before + step * max(0, int(gain_per_step))), "gain"
    # 下限より下に居る相手を下限まで引き上げない（下がる判定で上がらない）。
    return max(min(before, AFFINITY_FLOOR), before + step * max(0, int(loss_per_step))), "loss"


def action_of(text):
    """行動の印（<行動: …>）の中身。印でなければ None。"""
    found = ACTION_PATTERN.match(text)
    return found.group(1).strip() if found else None


def player_engaged(messages):
    """判定する会話か。プレイヤーが何か言ったか、開け閉め以外の行動をしたか。"""
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
    新しい system を末尾に足さないのは、system を先頭にしか置けないチャットテンプレートがあるため。
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


def without_added(data):
    """判定の2項目を除いた辞書。"""
    return {key: value for key, value in data.items() if key not in ADDED_FIELDS}


def restore(raw, structure):
    """足した型で返ってきた答えを、元の型の形に戻す。

    返る形（pydantic のモデル・辞書・JSON 文字列）はプロバイダで変わるので（`llm.as_dict`）、
    来た形に合わせて戻す。戻せなければそのまま返す（足した型は元の型の子なので、ゲームはそのまま読める）。
    """
    if isinstance(raw, dict):
        return without_added(raw)
    if isinstance(raw, str):
        data = llm.as_dict(raw)
        return json.dumps(without_added(data), ensure_ascii=False) if data is not None else raw
    dump = getattr(raw, "model_dump", None)
    if callable(dump):
        data = without_added(dump())
        validate = getattr(structure, "model_validate", None)
        return validate(data) if callable(validate) else structure(**data)
    return raw


# ============================================================ 本体
def apply(ctx):
    write = ctx.logger(LOG_BASENAME)
    store = _store()
    if store["worlds"] is None:
        store["worlds"] = loader_state.WorldStore(ctx, STATE_DIRNAME, write=write)
    worlds = store["worlds"].rebind(ctx, write)
    #: 要約の関数から send_request へ「この要約は判定を足す」を渡す。同じスレッドで降りてくる。
    pending = threading.local()
    #: 元の型 -> 足した型。要約の型は毎回同じなので1度だけ作る。鍵の型も持って id の使い回しを避ける。
    extended_types = {}

    def npc_id_of(app, character):
        value = getattr(character, "id", None)
        if value is not None and not isinstance(value, bool):
            return str(value)
        current = getattr(app, "in_conversation", None)
        return str(current) if current not in (None, False, True) else ""

    def player_name(app):
        name = getattr(getattr(app, "player", None), "name", None)
        return name.strip() if isinstance(name, str) and name.strip() else "プレイヤー"

    def affinity_of(character):
        relationship = getattr(character, "relationship", None)
        row = relationship.get("player") if isinstance(relationship, dict) else None
        value = row.get("affinity") if isinstance(row, dict) else None
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        return value

    def write_affinity(app, npc_id, character, value):
        """好感度を書く。実行時の人物と、素データの心当たり全部（`npcs.npc_stores`）。"""
        targets = [character, ui.character_of(app, npc_id)]
        for target in targets:
            relationship = getattr(target, "relationship", None)
            row = relationship.get("player") if isinstance(relationship, dict) else None
            if isinstance(row, dict):
                row["affinity"] = value
        for where, holder in npc_stores(app):
            if "characters" in where.rsplit(".", 1)[-1]:
                continue                # 実行時の名簿。素データではない
            data = holder.get(str(npc_id))
            relationship = data.get("relationship") if isinstance(data, dict) else None
            row = relationship.get("player") if isinstance(relationship, dict) else None
            if isinstance(row, dict):
                row["affinity"] = value

    def begin(app, character, messages):
        """この要約に判定を足すか決める。足すなら頼みの控え（dict）、足さないなら None。"""
        npc_id = npc_id_of(app, character)
        name = getattr(character, "name", None) or npc_id
        if not npc_id or npc_id.startswith(MOD_NPC_PREFIX) or npc_id == "player":
            return None
        if affinity_of(character) is None:
            write("skip: {} ({}) has no affinity".format(name, npc_id))
            return None
        if not player_engaged(messages):
            write("skip: {} ({}) the player only opened and closed the talk".format(name, npc_id))
            return None
        return {"app": app, "character": character, "npc_id": npc_id, "name": name,
                "player": player_name(app), "answer": None, "sent": False}

    def extended(structure):
        """要約の型に判定の2項目を足した子の型。作れなければ None。"""
        known = extended_types.get(id(structure))
        if known is not None and known[0] is structure:
            return known[1]
        module = llm.manager()
        factory = getattr(module, "create_model", None) if module is not None else None
        if not callable(factory):
            return None
        child = factory(getattr(structure, "__name__", "Result"), __base__=structure,
                        **{CHANGE_FIELD: (int, ...), REASON_FIELD: (str, ...)})
        extended_types[id(structure)] = (structure, child)
        return child

    def prepare(request, args, kwargs):
        """足した形の `(args, kwargs)`。足せなければ None（元のまま送る）。"""
        if len(args) > 2:
            structure = args[2]
        else:
            structure = kwargs.get("structure")
        if structure is None:
            write("skip: {} the summary had no structure".format(request["name"]))
            return None
        message = args[1] if len(args) > 1 else kwargs.get("message")
        text = INSTRUCTION.format(name=request["name"], player=request["player"])
        new_message = with_instruction(message, text)
        if new_message is None:
            write("skip: {} the summary had no system message".format(request["name"]))
            return None
        child = extended(structure)
        if child is None:
            write("skip: {} cannot extend the summary structure".format(request["name"]))
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
            request = getattr(pending, "request", None)
            manager_name = args[0] if args else kwargs.get("manager_name")
            if request is None or request["sent"] or manager_name != RESOLVER_MANAGER:
                return orig(*args, **kwargs)
            request["sent"] = True              # 要約1回に1度だけ
            try:
                prepared = prepare(request, args, kwargs)
            except Exception:
                ctx.log_exc("conversation affinity: cannot extend the summary request")
                prepared = None
            if prepared is None:
                return orig(*args, **kwargs)
            new_args, new_kwargs, structure = prepared
            try:
                raw = orig(*new_args, **new_kwargs)
            except Exception as exc:
                write("retry: {} the extended summary failed ({}: {}); sending it as is".format(
                    request["name"], type(exc).__name__, exc))
                return orig(*args, **kwargs)
            try:
                request["answer"] = llm.as_dict(raw)
                return restore(raw, structure)
            except Exception:
                ctx.log_exc("conversation affinity: cannot restore the summary answer")
                return raw

    llm.watch_aliases(ctx, [SEND_TARGET], install_send, label="conversation affinity")

    def settle(request):
        """要約が返った後。判定を読んで好感度を書く。"""
        app, character = request["app"], request["character"]
        npc_id, name = request["npc_id"], request["name"]
        if not request["sent"]:
            write("skip: {} ({}) the summary did not pass send_request".format(name, npc_id))
            return
        answer = request["answer"]
        if not isinstance(answer, dict):
            write("skip: {} ({}) no answer".format(name, npc_id))
            return
        step = clamp_step(answer.get(CHANGE_FIELD))
        reason = frames.short(str(answer.get(REASON_FIELD) or "").strip(), REASON_CHARS)
        if step is None:
            write("skip: {} ({}) unreadable change {!r}".format(
                name, npc_id, answer.get(CHANGE_FIELD)))
            return
        # 要約を待つ間にゲームが足した分（依頼のクリアの +20 など）を消さないよう、足す元はここで読む。
        before = affinity_of(character)
        if before is None:
            return
        day = ui.game_day(app)
        key = worlds.playthrough(app)
        with worlds.lock:
            bucket = worlds.load(key)
            gained_today = bool(DAILY_GAIN_ONCE and day is not None
                                and bucket.get(npc_id) == day)
        ceiling, limited_by = talk_affinity.ceiling(app, npc_id, TALK_CEILING)
        after, why = plan_change(before, step, gain_per_step=GAIN_PER_STEP,
                                 loss_per_step=LOSS_PER_STEP, ceiling=ceiling,
                                 gained_today=gained_today)
        write("judge: {} ({}) step {:+d} -> {} {} -> {} ({}){}".format(
            name, npc_id, step, why, before, after, reason,
            " [ceiling {} by {}]".format(ceiling, limited_by) if limited_by else ""))
        if after == before:
            return
        write_affinity(app, npc_id, character, after)
        if why == "gain" and day is not None:
            with worlds.lock:
                bucket = worlds.load(key)
                bucket[npc_id] = day
                worlds.save(key)

    @ctx.wrap(RESOLVER_TARGET, required=False, safe=True)
    def conversation_resolver(orig, *args, **kwargs):
        """要約に判定を相乗りさせる。引数は受け取ったまま `orig` へ渡し、戻り値もそのまま返す。"""
        request = None
        try:
            messages = kwargs.get("messages", args[1] if len(args) > 1 else None)
            character = kwargs.get("character_instance", args[3] if len(args) > 3 else None)
            app = ui.find_app()
            if app is not None and character is not None and isinstance(messages, list):
                request = begin(app, character, messages)
        except Exception:
            ctx.log_exc("conversation affinity: cannot prepare the judgment")
        previous = getattr(pending, "request", None)
        pending.request = request
        try:
            result = orig(*args, **kwargs)
        finally:
            pending.request = previous
        if request is not None:
            try:
                settle(request)
            except Exception:
                ctx.log_exc("conversation affinity: cannot apply the judgment")
        return result

    ctx.log("conversation affinity: installed (gain {}/step, loss {}/step, ceiling {}, "
            "daily {}, state {})".format(GAIN_PER_STEP, LOSS_PER_STEP, TALK_CEILING,
                                         "on" if DAILY_GAIN_ONCE else "off", worlds.dir_path()))
