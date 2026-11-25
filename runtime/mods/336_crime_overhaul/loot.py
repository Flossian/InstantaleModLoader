# -*- coding: utf-8 -*-
"""盗みの稼ぎ。自由行動の盗みの額に床と天井を付ける。仕様は DOC.md「盗みの稼ぎ」。

- 行動の締めの要約（`master_ai_process_summarizer*`。`lawfulness_loss` を返す側）の頼みに、
  「プレイヤーが不法に得た金銭の規模」を 0〜4 で答える項目を相乗りさせる（`337_` と同じ形。LLM を別に呼ばない）
- 規模ごとの目安は、その土地の依頼1件の報酬（ゲームの `get_quest_reward(土地の平均難易度)`）× 割合
- 行動の最初の facilitator の前に所持金を控え、要約の後の所持金との差を「LLM が渡した額」とする。
  0 なら目安を渡し、幅（目安の下限%〜上限%）の外なら幅の端へ寄せる
- 発覚したか（`lawfulness_loss`）は問わない。隠しおおせた盗みがいちばん得になる
"""
import threading

from instantale_modloader import frames, llm, loads, shopping, state, talk_summary, ui

from . import rules

MANAGER = "scripts.llm.llm_manager"
SEND_TARGET = MANAGER + ":send_request"
SHOP_TARGET = shopping.TARGETS[shopping.ENTER]   # ローダの窓口 `shopping` が1枚だけ包む
#: 行動の始まり（所持金を控える）。`119_` と同じ並び。綴りはゲーム側のまま。
FACILITATOR_TARGETS = (
    "master_ai_facilitator",
    "master_ai_facilitator_from_conversation",
    "master_ai_facilitator_in_quest",
    "master_ai_faciltiator_from_conversation_in_quest",
)
#: 行動の締め（判定を相乗りさせる）。`119_` と同じ並び。
#: 適用は `119_` より後（＝外側）なので、`lawfulness_loss` は `119_` が直した後の値が来る。
SUMMARIZER_TARGETS = (
    "master_ai_process_summarizer",
    "master_ai_process_summarizer_in_conversation",
    "master_ai_process_summarizer_in_conversation_in_quest",
    "master_ai_process_summarizer_in_quest",
    "master_ai_process_summarizer_with_no_recipients",
)
#: 要約の返却型をこの項目で見分ける（`Structure{summary, memory_recipients, lawfulness_loss}`）。
LAW_FIELD = "lawfulness_loss"
SCALE_FIELD = "loot_scale"
REASON_FIELD = "loot_reason"
ADDED_FIELDS = (SCALE_FIELD, REASON_FIELD)
INSTRUCTION = """【追加の判定】
要約とは別に、この行動でプレイヤー本人が金銭を手に入れた手段を判定し、loot_reason と loot_scale に答えること。
loot_reason: この行動の中で、プレイヤーが金銭を誰からどうやって受け取ったか（売った代金・稼ぎ・報酬・盗んだ・奪った など）を1文で書くこと。
loot_scale: 0〜4 の整数。1〜4 にするのは、この行動の中で、プレイヤーが盗み・すり・強盗・ゆすりによって、金銭そのものを相手の意に反して取ったときだけ。
- 0: 金銭を不法に得ていない。次はすべて 0 とすること
  - 品物を売った代金。代金は買い手が納得して払った金なので、売った品物が盗品・略奪品・倒した相手から奪った物でも 0。品物を盗んだこと自体は、盗んだときの行動で裁かれている
  - 入力文の乱暴な言い回し（奪い取ってきた・血塗られた 等）は問わない。この行動で金銭を受け取った手段だけを見る
  - 物を作って売った売上、働いた稼ぎ、依頼の報酬
  - 暴行・殺人・器物損壊のように金を取っていない犯罪、品物だけを盗んだ場合
  - NPC や第三者が奪った場合、プレイヤーが試みて失敗した場合
- 1: 小銭（財布を一つすった、少額をゆすった）
- 2: まとまった額（店のレジ、一人の有り金）
- 3: 大金（商店の金庫、裕福な屋敷、商隊の売上）
- 4: 財産（銀行や領主の宝物庫、大商会の蓄え）
発覚したかどうかは問わない。隠しおおせた盗みも数える。
summary にはこの判定のことを書かないこと。"""
LOOT_TOTAL_TEXT = "盗みの稼ぎは合わせて{total}ゴールドになった。"
LOOT_CUT_TEXT = "奪った金のうち、手元に残ったのは{total}ゴールドだった。"


def has_field(structure, name):
    """pydantic の型が `name` の項目を持つか。"""
    fields = llm.structure_fields(structure)
    return isinstance(fields, dict) and name in fields


def with_instruction(message, text):
    """先頭の system の本文に `text` を書き足した写し。system が無ければ None（`337_` と同じ。`llm.edit_turn`）。"""
    return llm.edit_turn(message, lambda content: content.rstrip() + "\n\n" + text)


def restore(raw, structure):
    """足した型で返ってきた答えを元の型の形に戻す（`337_` と同じ。要約の記録とセーブに判定を混ぜない）。"""
    return talk_summary.restore(raw, structure, ADDED_FIELDS)


def loot_shares(cfg):
    """規模 → 目安の割合（%）。設定が書き込まれた後に読むので関数にしてある。"""
    return {1: cfg.LOOT_SHARE_PETTY, 2: cfg.LOOT_SHARE_MODEST,
            3: cfg.LOOT_SHARE_LARGE, 4: cfg.LOOT_SHARE_FORTUNE}


def install(env):
    ctx, write, screen, cfg = env.ctx, env.write, env.screen, env.cfg
    area_difficulty, quest_reward, refresh_gold = env.area_difficulty, env.quest_reward, env.refresh_gold
    lock = threading.Lock()
    #: いまの行動。最初の facilitator で開き、要約で閉じる。
    action = {"open": False, "gold": None, "claimed": 0, "calls": 0, "key": None}
    #: 要約の関数から send_request へ「この要約に判定を足す」を渡す。同じスレッドで降りてくる。
    pending = threading.local()

    def open_action():
        app = ui.find_app()
        with lock:
            if not action["open"]:
                action.update(open=True, gold=ui.gold_of(app) if app is not None else None,
                              claimed=0, calls=0,
                              key=state.playthrough_key(app) if app is not None else None)

    def note_claims(result):
        claimed = rules.gold_claims(frames.field(result, "process"))
        with lock:
            if action["open"]:
                action["claimed"] += claimed
                action["calls"] += 1

    def close_action():
        with lock:
            taken = dict(action)
            action.update(open=False, gold=None, claimed=0, calls=0, key=None)
        return taken

    def drop_open_action(why):
        """要約まで届かずに開いたままの行動を閉じる（戦闘から逃げた・倒れた後など）。

        開いたままだと、行動の始めの所持金が次の行動まで持ち越され、その間の売却や
        別の周回の所持金まで「盗んだ額」に数えて、上限を超えた分を削っていた。
        店に入る・ロードするのは行動の途中では起きないので、そこで閉じる。
        """
        taken = close_action()
        if taken["open"]:
            write("loot: closed the action left open ({}; gold at its start {})".format(
                why, taken["gold"]))

    def shop_closes_action(call):
        """店に入る前（ローダの窓口 `shopping`。TECH.md §3.3.21）。"""
        if cfg.LOOT_ENABLED:
            try:
                drop_open_action("a shop opened")
            except Exception:
                ctx.log_exc("crime incentive: cannot close the action")

    # 持ち主は機能ごとに分ける（`law` も同じ場面に登録する。登録の順は前の包みの順のまま）。
    shopping.on(env.owner + ":loot", ctx, (shopping.ENTER,), before=shop_closes_action, write=write)

    def load_closes_action(call):
        """ロードの前（ローダの窓口 `loads`。TECH.md §3.3.21）。"""
        if cfg.LOOT_ENABLED:
            try:
                drop_open_action("a save was loaded")
            except Exception:
                ctx.log_exc("crime incentive: cannot close the action")

    loads.on(env.owner + ":loot", ctx, (loads.LOAD,), before=load_closes_action, write=write)

    def wrap_facilitator(name):
        @ctx.wrap("{}:{}".format(MANAGER, name), required=False, safe=True)
        def facilitator(orig, *args, **kwargs):
            if cfg.LOOT_ENABLED:
                try:
                    open_action()
                except Exception:
                    ctx.log_exc("crime incentive: cannot open the action")
            try:
                result = orig(*args, **kwargs)
            except Exception:
                if cfg.LOOT_ENABLED:
                    close_action()      # 要約まで届かない行動の所持金を、次の行動へ持ち越さない
                raise
            if cfg.LOOT_ENABLED:
                try:
                    note_claims(result)
                except Exception:
                    ctx.log_exc("crime incentive: cannot read the processes")
            return result
        return facilitator

    def extended(structure):
        # 理由（金をどう受け取ったか）を規模より先に書かせる。売買を盗みと取り違えにくくなる（VERIFICATION.md §3.84）
        return llm.extend_structure(structure, {REASON_FIELD: (str, ...), SCALE_FIELD: (int, ...)})

    def prepare(args, kwargs, structure):
        """足した形の `(args, kwargs)`。足せなければ None（元のまま送る）。"""
        new_message = with_instruction(frames.arg(args, kwargs, "message", 1), INSTRUCTION)
        if new_message is None:
            write("skip: the summary had no system message")
            return None
        child = extended(structure)
        if child is None:
            write("skip: cannot extend the summary structure")
            return None
        # どちらにも届いていない引数は名前で足す（前の手書きと同じ。`send_request(self, message, structure)`）
        args, kwargs, _done = frames.replace_arg(args, kwargs, "message", 1, new_message, insert=True)
        args, kwargs, _done = frames.replace_arg(args, kwargs, "structure", 2, child, insert=True)
        return args, kwargs

    def install_send(target):
        # 足した型で答えを受けて元の型に戻し、失敗したら素の型で送り直す（戻り値を変える）ので、
        # 窓口 `llm.on_request`（戻り値を変えない）に乗らない。自分で包むまま。
        @ctx.wrap(target, required=False)
        def send_request(orig, *args, **kwargs):
            request = getattr(pending, "request", None)
            if request is None or request["sent"]:
                return orig(*args, **kwargs)
            structure = frames.arg(args, kwargs, "structure", 2)
            if structure is None or not has_field(structure, LAW_FIELD):
                return orig(*args, **kwargs)
            request["sent"] = True              # 要約1回に1度だけ
            try:
                prepared = prepare(args, kwargs, structure)
            except Exception:
                ctx.log_exc("crime incentive: cannot extend the summary request")
                prepared = None
            if prepared is None:
                return orig(*args, **kwargs)
            try:
                raw = orig(*prepared[0], **prepared[1])
            except Exception as exc:
                write("retry: the extended summary failed ({}: {}); sending it as is".format(
                    type(exc).__name__, exc))
                return orig(*args, **kwargs)
            try:
                request["answer"] = llm.as_dict(raw)
                return restore(raw, structure)
            except Exception:
                ctx.log_exc("crime incentive: cannot restore the summary answer")
                return raw

    def settle(name, request, taken, result):
        """要約が返った後。規模を読んで所持金を整える。"""
        answer = request["answer"]
        if not request["sent"] or not isinstance(answer, dict):
            write("skip: {} no judgment (sent={})".format(name, request["sent"]))
            return
        scale = rules.clamp_scale(answer.get(SCALE_FIELD))
        reason = str(answer.get(REASON_FIELD) or "").strip()[:80]
        loss = frames.field(result, LAW_FIELD)
        app = ui.find_app()
        if taken.get("key") and app is not None and state.playthrough_key(app) != taken["key"]:
            write("skip: {} the action began in another playthrough ({!r})".format(name, taken["key"]))
            return
        now = ui.gold_of(app) if app is not None else None
        gained = now - taken["gold"] if now is not None and taken["gold"] is not None else None
        if scale is None:
            write("skip: {} unreadable scale {!r}".format(name, answer.get(SCALE_FIELD)))
            return
        if scale == 0:
            if gained or taken["claimed"] or loss:
                write("none: {} scale 0 gained={} claimed={} loss={!r} ({})".format(
                    name, gained, taken["claimed"], loss, reason))
            return
        if gained is None:
            write("skip: {} scale {} but the gold before the action is unknown "
                  "(facilitator calls {})".format(name, scale, taken["calls"]))
            return
        difficulty = area_difficulty(app)
        reward = quest_reward(difficulty)
        guide = rules.guide_amount(reward, loot_shares(cfg)[scale])
        delta, why = rules.plan_loot(max(0, gained), guide, cfg.LOOT_FLOOR_PCT, cfg.LOOT_CEILING_PCT)
        if delta < 0:
            delta = max(delta, -now)            # 所持金を負にしない
        after = ui.add_gold(app, delta) if delta else now
        if after is None:
            write("WARN {}: cannot change the gold by {}".format(name, delta))
            return
        total = max(0, gained) + delta
        write("loot: {} scale {} {} gained={} claimed={} guide={} (difficulty {} reward {}) "
              "-> {:+d}, gold {} -> {} loss={!r} ({})".format(
                  name, scale, why, gained, taken["claimed"], guide, difficulty, reward,
                  delta, now, after, loss, reason))
        if not delta:
            return

        def show():
            refresh_gold(app)
            if cfg.LOOT_NOTICE:
                text = LOOT_CUT_TEXT if delta < 0 else LOOT_TOTAL_TEXT
                screen.say(app, ui.rewrite_coins(text.format(total=ui.money(total))))
        screen.schedule(show)

    def wrap_summarizer(name):
        @ctx.wrap("{}:{}".format(MANAGER, name), required=False, safe=True)
        def summarizer(orig, *args, **kwargs):
            if not cfg.LOOT_ENABLED:
                return orig(*args, **kwargs)
            request = {"sent": False, "answer": None}
            previous = getattr(pending, "request", None)
            pending.request = request
            try:
                result = orig(*args, **kwargs)
            except Exception:
                close_action()
                raise
            finally:
                pending.request = previous
            try:
                settle(name, request, close_action(), result)
            except Exception:
                ctx.log_exc("crime incentive: cannot settle the loot")
            return result
        return summarizer

    for target_name in FACILITATOR_TARGETS:
        wrap_facilitator(target_name)
    for target_name in SUMMARIZER_TARGETS:
        wrap_summarizer(target_name)
    llm.watch_aliases(ctx, [SEND_TARGET], install_send, label="crime incentive")
