# -*- coding: utf-8 -*-
"""会話の中身で NPC の好感度が上下する。

素のゲームの好感度（`relationship.player.affinity`）は依頼のクリアで同行者に +20 され、
会話ではほとんど動かない（会話を閉じた192回のうち1回。GAME.md §2.25.1）。
このMODは会話を閉じるたびに、その会話で相手の気持ちがどう動いたかを LLM に -3〜+3 で1回だけ聞き、
好感度へ足す。仕様と決めた経緯は DOC.md。

##### どこで聞くか

会話の要約（`llm_manager:conversation_resolver`）を包み、`orig` の**前**に聞く。
要約はゲームが会話の終わりに別スレッドで回すもので（`ConversationEndManager.resolve_conversation`）、
会話の書き起こし（`messages`）と相手（`character_instance`）がそろって渡ってくる。
ここで待っても画面は止まらない。
感情の文（`affinity_text`）はゲームが会話の始まりと終わり、依頼のクリアで好感度から作り直すので
（GAME.md §2.25.1）、この MOD は好感度の数だけを書く。

##### 釣り合い

依頼のクリア1回が同行者全員に +20 なので、会話はそれより小さく動かす。

- 上がるときは1段 `GAIN_PER_STEP`（既定 2、最大 +6）、下がるときは1段 `LOSS_PER_STEP`（既定 4、最大 -12）
- 会話だけで上げられるのは `TALK_CEILING`（既定 60＝「仲間だと感じている」）まで。それより上は冒険を共にして上げる
- 上がるのは同じ相手から1日1回まで（日数は宿泊・街の移動でしか進まないので、同じ街で話し続けても稼げない）。
  下がる方は毎回効く
- プレイヤーが一言も話していない会話（話しかけて閉じただけ）は聞かない
- MOD の NPC（id が `mod:` で始まる。330 の管理人・331 の主人）は対象にしない
"""

import sys

from instantale_modloader import frames, llm, state as loader_state, ui
from instantale_modloader.npcs import npc_stores

# ---- 設定（既定値は mod.json の "settings" と一致させること。
#      `tools/check_mods.py` が AST で突き合わせる）------------------------
GAIN_PER_STEP = 2             # 上がる1段あたりの好感度
LOSS_PER_STEP = 4             # 下がる1段あたりの好感度
TALK_CEILING = 60             # 会話だけで上げられる好感度の上限
DAILY_GAIN_ONCE = True        # 同じ相手から上がるのは1日1回
SHOW_CHANGE = True            # 動いたら本文に一言出す
GAIN_TEXT = "{name}は{player}に少し心を開いたようだ。"
LOSS_TEXT = "{name}の機嫌を損ねたようだ。"

# ---- 設定にしない定数 ----------------------------------------------------
LOG_BASENAME = "conversation_affinity.log"
STATE_DIRNAME = "conversation_affinity"
STORE_ATTR = "_instantale_conversation_affinity"
RESOLVER_TARGET = "scripts.llm.llm_manager:conversation_resolver"
MANAGER_NAME = "mod_conversation_affinity"
TIMEOUT = 60                  # 秒。返らなければ動かさない
STEP_LIMIT = 3                # 段の絶対値の上限
TRANSCRIPT_CHARS = 3000       # 頼み文に渡す書き起こしの上限（末尾を残す）
PROFILE_CHARS = 300
REASON_CHARS = 80
AFFINITY_FLOOR = -100         # 好感度の下限（ゲームの段は -41 以下が最下段）
MOD_NPC_PREFIX = "mod:"
ACTION_PREFIXES = ("<行動:", "<行動：", "＜行動:", "＜行動：")

PROMPT = """あなたは RPG の NPC の心の動きを判定する係です。
以下は、プレイヤーの「{player}」と NPC の「{name}」の会話です。

# {name}
{profile}
{player}への今の気持ち: {feeling}

# 会話（user が {player}、assistant が {name}）
{transcript}

# 判定すること
この会話で、{name}の{player}への好感がどう動いたかを -3〜+3 の整数で答えてください。
- 0: 変わらない。用件だけのやり取りや普通の雑談はほぼ 0
- +1: 少し好感を持った / +2: はっきり好感を持った / +3: 強く心を動かされた
- -1: 少し不快だった / -2: はっきり不快だった / -3: 強い怒りや屈辱を感じた
{name}の性格と立場から判断してください。{player}がおだてたり媚びたりしただけでは上げないでください。
reason には判定の理由を{name}の立場から1文で書いてください。
"""


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


def player_spoke(messages):
    """プレイヤーが行動の印（<行動: …>）以外を一言でも書いたか。"""
    for turn in messages or []:
        if not isinstance(turn, dict) or turn.get("role") != "user":
            continue
        text = str(turn.get("content") or "").strip()
        if text and not text.startswith(ACTION_PREFIXES):
            return True
    return False


def format_text(template, **fields):
    """設定の文型に値を入れる。鍵が足りなくても落とさない（空にする）。"""
    if not isinstance(template, str) or not template.strip():
        return ""

    class _Missing(dict):
        def __missing__(self, key):
            return ""

    try:
        return template.format_map(_Missing(fields)).strip()
    except (ValueError, IndexError):
        return ""


# ============================================================ 本体
def apply(ctx):
    write = ctx.logger(LOG_BASENAME)
    store = _store()
    if store["worlds"] is None:
        store["worlds"] = loader_state.WorldStore(ctx, STATE_DIRNAME, write=write)
    worlds = store["worlds"].rebind(ctx, write)
    screen = ui.Screen(ctx, write, tag="conversation affinity")

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

    def transcript_of(messages, app, character):
        """会話の書き起こし。ゲーム自身の書き起こしがあればそれ（`311_` と同じ）、無ければ役割と本文。"""
        context_manager = sys.modules.get("scripts.llm.context_manager")
        to_text = getattr(context_manager, "conversation_history_to_text", None) \
            if context_manager else None
        if to_text is not None:
            try:
                text = to_text(messages, getattr(app, "player", None), character)
                if isinstance(text, str) and text.strip():
                    return text.strip()[-TRANSCRIPT_CHARS:]
            except Exception as exc:
                write("conversation_history_to_text failed: {}: {}".format(
                    type(exc).__name__, exc))
        lines = ["{}: {}".format(turn.get("role", "?"), frames.short(turn.get("content"), 400))
                 for turn in messages or [] if isinstance(turn, dict)]
        return "\n".join(lines)[-TRANSCRIPT_CHARS:]

    def profile_of(character):
        lines = []
        for label, name in (("経歴", "profile"), ("性格", "personality"),
                            ("話し方", "speech_style"), ("職業", "job")):
            value = getattr(character, name, None)
            if isinstance(value, str) and value.strip():
                lines.append("{}: {}".format(label, frames.short(value.strip(), PROFILE_CHARS)))
        return "\n".join(lines) or "（不明）"

    def feeling_of(character, emotion_text):
        relationship = getattr(character, "relationship", None)
        row = relationship.get("player") if isinstance(relationship, dict) else None
        text = row.get("affinity_text") if isinstance(row, dict) else None
        if isinstance(text, list):
            text = text[0] if text else None
        if isinstance(text, str) and text.strip():
            return text.strip()
        return frames.short(str(emotion_text or ""), 80) or "（不明）"

    def judge(app, character, messages, emotion_text):
        """LLM に段を聞く。`(段, 理由)`。聞けなければ `(None, 理由の文)`。"""
        structure = llm.create_structure(ctx, "ConversationAffinity", {
            "change": (int, ...), "reason": (str, ...)}, label="conversation affinity")
        if structure is None:
            return None, "no structure"
        name = getattr(character, "name", None) or "相手"
        prompt = PROMPT.format(player=player_name(app), name=name,
                               profile=profile_of(character),
                               feeling=feeling_of(character, emotion_text),
                               transcript=transcript_of(messages, app, character))
        raw = llm.ask(ctx, MANAGER_NAME, [{"role": "user", "content": prompt}],
                      timeout=TIMEOUT, structure=structure, label="conversation affinity",
                      write=write)
        if not isinstance(raw, dict):
            return None, "no answer"
        step = clamp_step(raw.get("change"))
        reason = frames.short(str(raw.get("reason") or "").strip(), REASON_CHARS)
        if step is None:
            return None, "unreadable change {!r}".format(raw.get("change"))
        return step, reason

    def settle(app, character, messages, emotion_text):
        """1回の会話ぶん。動かしたら本文に出す文を返す（無ければ ""）。"""
        npc_id = npc_id_of(app, character)
        name = getattr(character, "name", None) or npc_id
        if not npc_id or npc_id.startswith(MOD_NPC_PREFIX) or npc_id == "player":
            return ""
        before = affinity_of(character)
        if before is None:
            write("skip: {} ({}) has no affinity".format(name, npc_id))
            return ""
        if not player_spoke(messages):
            write("skip: {} ({}) the player said nothing".format(name, npc_id))
            return ""
        day = ui.game_day(app)
        key = worlds.playthrough(app)
        with worlds.lock:
            bucket = worlds.load(key)
            gained_today = bool(DAILY_GAIN_ONCE and day is not None
                                and bucket.get(npc_id) == day)
        step, reason = judge(app, character, messages, emotion_text)
        if step is None:
            write("skip: {} ({}) {}".format(name, npc_id, reason))
            return ""
        # 判定を待つ間（最長 TIMEOUT 秒）にゲームが足した分（依頼のクリアの +20 など）を消さないよう、
        # 足す元は判定の後に読み直す。
        now = affinity_of(character)
        if now is not None and now != before:
            write("judge: {} ({}) affinity moved {} -> {} while judging".format(
                name, npc_id, before, now))
            before = now
        after, why = plan_change(before, step, gain_per_step=GAIN_PER_STEP,
                                 loss_per_step=LOSS_PER_STEP, ceiling=TALK_CEILING,
                                 gained_today=gained_today)
        write("judge: {} ({}) step {:+d} -> {} {} -> {} ({})".format(
            name, npc_id, step, why, before, after, reason))
        if after == before:
            return ""
        write_affinity(app, npc_id, character, after)
        if why == "gain" and day is not None:
            with worlds.lock:
                bucket = worlds.load(key)
                bucket[npc_id] = day
                worlds.save(key)
        template = GAIN_TEXT if after > before else LOSS_TEXT
        return format_text(template, name=name, player=player_name(app))

    @ctx.wrap(RESOLVER_TARGET, required=False, safe=True)
    def conversation_resolver(orig, *args, **kwargs):
        """会話の要約の前に、好感度の動きを聞いて書く。引数は受け取ったまま `orig` へ渡す。"""
        try:
            messages = kwargs.get("messages", args[1] if len(args) > 1 else None)
            character = kwargs.get("character_instance", args[3] if len(args) > 3 else None)
            emotion_text = kwargs.get("emotion_scores_text", args[4] if len(args) > 4 else None)
            app = ui.find_app()
            if app is not None and character is not None and isinstance(messages, list):
                line = settle(app, character, messages, emotion_text)
                if line and SHOW_CHANGE:
                    screen.when_idle(app, lambda: screen.say(app, line),
                                     proceed_on_timeout=True, tag="conversation affinity")
        except Exception:
            ctx.log_exc("conversation affinity: cannot judge the conversation")
        return orig(*args, **kwargs)

    ctx.log("conversation affinity: installed (gain {}/step, loss {}/step, ceiling {}, "
            "daily {}, state {})".format(GAIN_PER_STEP, LOSS_PER_STEP, TALK_CEILING,
                                         "on" if DAILY_GAIN_ONCE else "off", worlds.dir_path()))
