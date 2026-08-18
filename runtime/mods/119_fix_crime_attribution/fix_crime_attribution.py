# -*- coding: utf-8 -*-
"""NPC の犯罪をプレイヤーへ帰属する誤判定を防ぐ。

ゲームの自由行動は facilitator が処理を決め、
その結果を summarizer が記憶と `lawfulness_loss` にまとめる。
実測では facilitator が「プレイヤーは介入していない」と判断して
`process=[]` を返した直後、summarizer だけが `lawfulness_loss=10` を返し、
プレイヤーが犯罪者になった。

この MOD は二段で防ぐ。

1. 実測済み system 文を置換し、行為主体を明示的に判定させる。
2. LLM が出した機械可読マーカーが `other_or_none` の場合だけ、
   manager の戻り値から評判低下と逮捕を除く。

マーカー欠落・未知の戻り値・書換失敗では従来結果を維持する。
本物の犯罪を推測で消すより、誤判定を残して監査ログに出す方を選ぶ。

## 二段のうち、どちらがどの経路で効くか

戻り値を直す2段目は `llm_manager` の `master_ai_*` を包む＝プロバイダに依存しないので、
ローカルでもクラウドでも同じように効く。
効かなかったのは1段目（プロンプトの置換）で、
v1 は `LlamaCppClient.chat` しか包んでいなかった。
クラウド（APIキー）ではそこを通らないので system 文が書き換わらず、
LLM はマーカーを出さない。
マーカーが無ければ2段目は既定の素通しに倒れる。
**クラウドでは MOD 全体が何もしていなかった**（v2 で修正）。

仕掛ける場所は `instantale_modloader.llm` が持っている（TECH.md
§3.2.3 の「写して回るものが出たら、それはローダの語彙」）。
ローカルの3点とクラウドの `llm_manager` 別名、
別名の後生えの見張りまでそちらの担当で、この MOD が持つのはどう書き換えるかだけ。
`111_llm_prompt_replace` も同じ口を使う。

二度当たっても壊れない（`INJECT_MARKER` が本文にあれば
`already_injected` で何もしない）ので、印が届かない別スレッド経路でも安全。
"""


from instantale_modloader.llm import wrap_outgoing


LOG_BASENAME = "crime_attribution_fix.log"

FACILITATOR_TARGETS = (
    "master_ai_facilitator",
    "master_ai_facilitator_from_conversation",
    "master_ai_facilitator_in_quest",
    "master_ai_faciltiator_from_conversation_in_quest",  # ゲーム側の綴り
)

SUMMARIZER_TARGETS = (
    "master_ai_process_summarizer",
    "master_ai_process_summarizer_in_conversation",
    "master_ai_process_summarizer_in_conversation_in_quest",
    "master_ai_process_summarizer_in_quest",
    "master_ai_process_summarizer_with_no_recipients",
)

MARK_PLAYER = "【crime_actor:player】"
MARK_OTHER = "【crime_actor:other_or_none】"
INJECT_MARKER = "【犯罪帰属MOD】"

ARREST_ANCHOR = (
    "- arrest_player: プレイヤーを逮捕する。懲役刑を受けることになる。"
)

LAW_ANCHOR = (
    "- lawfulness_loss: プレイヤーの行動結果が犯罪または著しく非道徳的な内容であり、"
    "それを外部が感知していた場合、1-10の尺度でエリア内の評判低下量を決定する。"
    "1がただの嫌がらせ、5が暴行などの軽犯罪、10が凶悪犯罪。"
    "悪事をしてもうまく隠しおおせたか、そもそも違法にあたらない行動であった場合には"
    "0と指定すること。"
)

ATTRIBUTION_RULES = (
    "\n{marker}\n"
    "【犯罪主体の判定 ― 最優先】\n"
    "- 会話ログの各行がプレイヤー名で始まっていても、それだけでプレイヤーが"
    "行為を実行したとみなしてはならない。入力欄の話者ではなく、物語上の実行主体を判定する。\n"
    "- プレイヤー本人が違法行為を実行、共謀、教唆、または強要した場合だけ player とする。\n"
    "- NPC がプレイヤーや第三者を加害した場合、第三者同士の出来事、プレイヤー不在の"
    "出来事、プレイヤーが観測・叙述・会話・助言しただけの場合は other_or_none とする。\n"
).format(marker=INJECT_MARKER)

FACILITATOR_REPLACEMENT = (
    "- arrest_player: プレイヤー本人が違法行為を実行、共謀、教唆、または強要し、"
    "逮捕が相応しい場合に限りプレイヤーを逮捕する。NPC や第三者の犯罪、"
    "プレイヤーが被害者・観測者・叙述者にすぎない場合には使用しない。"
    + ATTRIBUTION_RULES
    + "- think の先頭には、判定結果として必ず "
    + MARK_PLAYER
    + " または "
    + MARK_OTHER
    + " のどちらか一つを付ける。これは一時的な機械判定用マーカーである。\n"
)

SUMMARIZER_REPLACEMENT = (
    "- lawfulness_loss: 物語上でプレイヤー本人が違法行為を実行、共謀、教唆、"
    "または強要し、その結果を外部が感知した場合だけ1-10を指定する。"
    "NPC や第三者の犯罪、プレイヤーが被害者・観測者・叙述者にすぎない場合、"
    "プレイヤー不在の出来事、違法でない行動、隠しおおせた行動は必ず0とする。"
    "1がただの嫌がらせ、5が暴行などの軽犯罪、10が凶悪犯罪。"
    + ATTRIBUTION_RULES
    + "- summary の先頭には、判定結果として必ず "
    + MARK_PLAYER
    + " または "
    + MARK_OTHER
    + " のどちらか一つを付ける。これは一時的な機械判定用マーカーであり、"
    "物語本文の一部ではない。\n"
)


def _get(container, name):
    if isinstance(container, dict):
        return container.get(name)
    return getattr(container, name, None)


def _set(container, name, value):
    try:
        if isinstance(container, dict):
            container[name] = value
            return True
        setattr(container, name, value)
        return getattr(container, name, None) == value
    except Exception:
        return False


def shape_of(value):
    if isinstance(value, dict):
        return "dict{" + ", ".join(sorted(str(k) for k in value)[:8]) + "}"
    if isinstance(value, (list, tuple)):
        return "{}[{}]".format(type(value).__name__, len(value))
    try:
        fields = sorted(vars(value))[:8]
    except Exception:
        fields = []
    return "{}({})".format(type(value).__name__, ", ".join(fields))


def actor_and_clean(text):
    """先頭マーカーを `(actor, マーカー除去後)` にする。無ければ `(None, text)`。"""
    if not isinstance(text, str):
        return None, text
    stripped = text.lstrip()
    prefix = text[:len(text) - len(stripped)]
    if stripped.startswith(MARK_PLAYER):
        return "player", prefix + stripped[len(MARK_PLAYER):].lstrip()
    if stripped.startswith(MARK_OTHER):
        return "other_or_none", prefix + stripped[len(MARK_OTHER):].lstrip()
    return None, text


def process_type(item):
    return _get(item, "type")


def postprocess_facilitator(result):
    """明示的に他者犯罪と判定された場合だけ `arrest_player` を除く。"""
    actor, cleaned = actor_and_clean(_get(result, "think"))
    if actor is None:
        return result, "marker_missing", 0
    if not _set(result, "think", cleaned):
        return result, "think_not_writable", 0
    if actor != "other_or_none":
        return result, "player_keep", 0

    process = _get(result, "process")
    if not isinstance(process, (list, tuple)):
        return result, "process_unknown", 0
    kept = [item for item in process if process_type(item) != "arrest_player"]
    removed = len(process) - len(kept)
    if not removed:
        return result, "other_no_arrest", 0
    replacement = tuple(kept) if isinstance(process, tuple) else kept
    if not _set(result, "process", replacement):
        return result, "process_not_writable", 0
    return result, "other_arrest_removed", removed


def postprocess_summarizer(result):
    """明示的に他者犯罪と判定された場合だけ `lawfulness_loss` を 0 にする。"""
    actor, cleaned = actor_and_clean(_get(result, "summary"))
    if actor is None:
        return result, "marker_missing", None
    if not _set(result, "summary", cleaned):
        return result, "summary_not_writable", None

    loss = _get(result, "lawfulness_loss")
    if actor != "other_or_none":
        return result, "player_keep", loss
    if isinstance(loss, bool) or not isinstance(loss, (int, float)):
        return result, "loss_unknown", loss
    if loss <= 0:
        return result, "other_zero", loss
    if not _set(result, "lawfulness_loss", 0):
        return result, "loss_not_writable", loss
    return result, "other_loss_zeroed", loss


def rewrite_texts(texts):
    """出ていく文章の並びのうち、対象 system 文だけを置換する。

    `(新しい並び, kind, reason)`。
    書き換えないときは渡された並びをそのまま返す（`kind` は None）。
    messages の組み直しはローダ側（`instantale_modloader.llm`）の担当なので、
    ここは文字列の並びしか見ない。

    どちらの目印が何個あるかで facilitator / summarizer を見分ける。
    片方がちょうど1つ、もう片方が0のときだけ書き換える。
    数が合わないものは知らない形なので、素通しして理由を残す。
    """
    if not isinstance(texts, (list, tuple)):
        return texts, None, "not_list"
    contents = [text for text in texts if isinstance(text, str)]
    blob = "\n".join(contents)
    if INJECT_MARKER in blob:
        return texts, None, "already_injected"

    arrest_count = blob.count(ARREST_ANCHOR)
    law_count = blob.count(LAW_ANCHOR)
    if arrest_count == 1 and law_count == 0:
        anchor, replacement, kind = ARREST_ANCHOR, FACILITATOR_REPLACEMENT, "facilitator"
    elif law_count == 1 and arrest_count == 0:
        anchor, replacement, kind = LAW_ANCHOR, SUMMARIZER_REPLACEMENT, "summarizer"
    elif arrest_count == 0 and law_count == 0:
        return texts, None, "not_target"
    else:
        return texts, None, "ambiguous_anchor"

    rewritten = []
    changed = 0
    for text in texts:
        if not isinstance(text, str) or anchor not in text:
            rewritten.append(text)
            continue
        rewritten.append(text.replace(anchor, replacement, 1))
        changed += 1
    if changed != 1:
        return texts, None, "anchor_change_count_{}".format(changed)
    return rewritten, kind, "rewritten"


def apply(ctx):
    log_path = ctx.out_path(LOG_BASENAME)
    rewritten, check_kind, check_reason = rewrite_texts([LAW_ANCHOR])
    verified = (
        check_kind == "summarizer"
        and check_reason == "rewritten"
        and INJECT_MARKER in rewritten[0]
        and rewrite_texts(rewritten)[2] == "already_injected"
    )
    state = {"enabled": verified, "missing": set(), "rewrites": 0}

    write = ctx.logger(LOG_BASENAME)

    def rewrite(texts, site):
        """ローダから呼ばれる。書き換えないときは None。"""
        if not state["enabled"]:
            return None
        new_texts, kind, reason = rewrite_texts(texts)
        if kind is not None:
            state["rewrites"] += 1
            write("prompt {} rewritten at {} (count={})".format(
                kind, site, state["rewrites"]))
            return new_texts
        if reason not in ("not_target", "already_injected"):
            # 知らない形は1種類につき1度だけ残す（毎回の推論で溢れさせない）。
            key = site + ":" + reason
            if key not in state["missing"]:
                state["missing"].add(key)
                write("prompt unchanged at {}: {}".format(site, reason))
        return None

    hooks = wrap_outgoing(
        ctx, rewrite, label="crime attribution fix",
        on_arm=lambda target: write("late-armed on {}".format(target)))

    def wrap_facilitator(name):
        @ctx.wrap("scripts.llm.llm_manager:{}".format(name), required=False)
        def facilitator(orig, *args, **kwargs):
            result = orig(*args, **kwargs)
            if not state["enabled"]:
                return result
            try:
                result, reason, removed = postprocess_facilitator(result)
                if removed or reason not in ("player_keep", "other_no_arrest"):
                    write("{}: {} removed={} shape={}".format(
                        name, reason, removed, shape_of(result)))
            except Exception:
                ctx.log_exc("crime attribution fix: {} postprocess failed".format(name))
            return result
        return facilitator

    def wrap_summarizer(name):
        @ctx.wrap("scripts.llm.llm_manager:{}".format(name), required=False)
        def summarizer(orig, *args, **kwargs):
            result = orig(*args, **kwargs)
            if not state["enabled"]:
                return result
            try:
                result, reason, previous = postprocess_summarizer(result)
                if reason != "player_keep" or previous:
                    write("{}: {} previous_loss={!r} shape={}".format(
                        name, reason, previous, shape_of(result)))
            except Exception:
                ctx.log_exc("crime attribution fix: {} postprocess failed".format(name))
            return result
        return summarizer

    for target_name in FACILITATOR_TARGETS:
        wrap_facilitator(target_name)
    for target_name in SUMMARIZER_TARGETS:
        wrap_summarizer(target_name)

    if verified:
        armed = hooks.armed()
        ctx.log("crime attribution fix installed; prompt sites {}; log {}".format(
            ", ".join(armed) if armed else "none yet (waiting for the alias)",
            log_path))
    else:
        ctx.log("crime attribution fix self-check failed: {!r}/{!r}".format(
            check_kind, check_reason), level="ERROR")
