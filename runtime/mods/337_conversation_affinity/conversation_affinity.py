# -*- coding: utf-8 -*-
"""会話の中身で NPC の好感度が上下する。

素のゲームの好感度（`relationship.player.affinity`）は依頼のクリアで同行者に +20 され、
会話ではほとんど動かない（会話を閉じた192回のうち1回。GAME.md §2.25.1）。
このMODは会話を閉じるたびに、その会話で相手の気持ちがどう動いたかを -3〜+3 で判定させ、
好感度へ足す。遊び方は DOC.md、決めた仕様と確認項目は VERIFICATION.md §3.89。

##### どこで聞くか（要約の頼みに相乗りする）

LLM を別に呼ばない。
ゲームが会話の終わりに回す要約（`llm_manager:conversation_resolver`）の頼みに、判定の項目を足す。
要約の頼みには相手の人物像・プレイヤーへの今の感情・会話の全文がもう載っているので、材料は足さなくてよい。
ゲームは要約が終わるまで会話を閉じきらないので（GAME.md §2.5）、別に呼ぶとその待ちがそのぶん延びた。

相乗りの包みはローダの窓口 `talk_summary`（TECH.md §3.3.16）が持つ。
版1はこの MOD が自分で `send_request` を包んでいたが、要約に乗る MOD が2本目（街の勢力）になったので窓口へ移した。

- 要約は同じスレッドで `llm_manager:send_request(manager_name, message, structure, ...)` を通る
  （`213_` の記録。ローカルでもクラウドでも同じ別名）。
  窓口はそこで `structure`（要約の返却型 `Result{summary}`）を、判定の2項目を足した子の型に差し替え、
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

import sys

from instantale_modloader import frames, state as loader_state, talk_affinity, talk_summary, ui
from instantale_modloader.npcs import npc_stores

# ---- 設定（既定値は mod.json の "settings" と一致させること。
#      `tools/check_mods.py` が AST で突き合わせる）------------------------
GAIN_PER_STEP = 2             # 上がる1段あたりの好感度
LOSS_PER_STEP = 4             # 下がる1段あたりの好感度
TALK_CEILING = 60             # 会話だけで上げられる好感度の上限
DAILY_GAIN_ONCE = True        # 同じ相手から上がるのは1日1回

# ---- 設定にしない定数 ----------------------------------------------------
OWNER = "337_conversation_affinity"
LOG_BASENAME = "conversation_affinity.log"
STATE_DIRNAME = "conversation_affinity"
STORE_ATTR = "_instantale_conversation_affinity"
CHANGE_FIELD = "affinity_change"
REASON_FIELD = "affinity_reason"
ADDED_FIELDS = (CHANGE_FIELD, REASON_FIELD)
STEP_LIMIT = 3                # 段の絶対値の上限
REASON_CHARS = 80
AFFINITY_FLOOR = -100         # 好感度の下限（ゲームの段は -41 以下が最下段）
MOD_NPC_PREFIX = talk_summary.MOD_NPC_PREFIX
TRIVIAL_ACTIONS = talk_summary.TRIVIAL_ACTIONS

INSTRUCTION = """【追加の判定】
要約とは別に、この会話で{name}の{player}への好感がどう動いたかを判定し、
affinity_change に -3〜+3 の整数で答えること。
- 0: 変わらない。用件だけのやり取りや普通の雑談はほぼ 0
- +1: 少し好感を持った / +2: はっきり好感を持った / +3: 強く心を動かされた
- -1: 少し不快だった / -2: はっきり不快だった / -3: 強い怒りや屈辱を感じた
{name}の性格・立場と、{player}に対する今の感情から判断すること。{player}がおだてたり媚びたりしただけでは上げないこと。
affinity_reason には判定の理由を{name}の立場から1文で書くこと。
summary にはこの判定のことを書かないこと。"""

#: 窓口の `talk["status"]` を、ログの理由へ。
STATUS_REASONS = {
    "not_sent": "the summary did not pass send_request",
    "no_structure": "the summary had no structure",
    "no_system": "the summary had no system message",
    "cannot_extend": "cannot extend the summary structure",
    "retried": "the extended summary failed and was sent as is",
    "unreadable": "no answer",
}


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


action_of = talk_summary.action_of
player_engaged = talk_summary.player_engaged
with_instruction = talk_summary.with_instruction


def restore(raw, structure):
    """足した型で返ってきた答えを、元の型の形に戻す（窓口の `restore`）。"""
    return talk_summary.restore(raw, structure, ADDED_FIELDS)


# ============================================================ 本体
def apply(ctx):
    write = ctx.logger(LOG_BASENAME)
    store = _store()
    if store["worlds"] is None:
        store["worlds"] = loader_state.WorldStore(ctx, STATE_DIRNAME, write=write)
    worlds = store["worlds"].rebind(ctx, write)

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

    def begin(talk):
        """この要約に判定を足すなら決まりの文、足さないなら None。"""
        npc_id, name, character = talk["npc_id"], talk["name"], talk["character"]
        if not npc_id or npc_id.startswith(MOD_NPC_PREFIX) or npc_id == "player":
            return None
        if affinity_of(character) is None:
            write("skip: {} ({}) has no affinity".format(name, npc_id))
            return None
        if not player_engaged(talk["messages"]):
            write("skip: {} ({}) the player only opened and closed the talk".format(name, npc_id))
            return None
        return INSTRUCTION.format(name=name, player=talk["player"])

    def settle(talk, answer):
        """要約が返った後。判定を読んで好感度を書く。"""
        app, character = talk["app"], talk["character"]
        npc_id, name = talk["npc_id"], talk["name"]
        if answer is None:
            write("skip: {} ({}) {}{}".format(
                name, npc_id, STATUS_REASONS.get(talk["status"], talk["status"]),
                " ({})".format(talk["detail"]) if talk.get("detail") else ""))
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

    talk_summary.install(ctx, write)
    talk_summary.ride(OWNER, ctx, begin, settle, {CHANGE_FIELD: int, REASON_FIELD: str})

    ctx.log("conversation affinity: installed (gain {}/step, loss {}/step, ceiling {}, "
            "daily {}, state {})".format(GAIN_PER_STEP, LOSS_PER_STEP, TALK_CEILING,
                                         "on" if DAILY_GAIN_ONCE else "off", worlds.dir_path()))
