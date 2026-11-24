# -*- coding: utf-8 -*-
"""会話から仲間と別れる（設定 `FAREWELL_ENABLED`）。

会話の相手が仲間なら「ここで別れる」を「会話を終了する」の手前に出し、確認を挟んでから外す。

- 相手は「会話を終了する」ボタンの `args[0]`（`ui.conversation_partner`）。名簿の在り処と形は決めつけない（`ui.party_ids`。GAME.md §2.8）
- 戦闘中と依頼の最中は出さない。`original_party` は判定に使わない（平常時も名簿と同じ内容で入っていて、雇用には追随しない。GAME.md §2.8）
- 置き先は外す前に決め、決まらない土地では別れない（置き先の無いまま外すと、その NPC は世界のどこにも居なくなる。GAME.md §2.8）
- 会話はゲームの経路で閉じてから外す（閉じずに進めると立ち絵が残る。GAME.md §2.5）。
  閉じるボタンの `end_text` に別れた旨を書くので、会話の要約とライフログに残る
- 外すのはゲームの `remove_party_member`、置くのは `move_npc_to_facility`。
  外している間は入口の `shared.departing` に控え、ゲームが自分で置いたら二重に置かない
- ボタンと確認の画面はローダの窓口 `choices` の宣言型の口（TECH.md §3.3.14）。
  押下はゲームの `process_choice` に乗せる（`LeavePhase`。画面の塗り替えをゲームに任せるため）
"""

from instantale_modloader import choices, ui

from .places import home_of, unpack

# ---------------------------------------------------------------- 文言
LEAVE_LABEL = "ここで別れる"
CONFIRM_QUESTION = "{name}とはここで別れることになる。よいか？"
CONFIRM_LABEL = "ああ、ここで別れよう"
CANCEL_LABEL = "やめておく"

# 古い版（`302_` の版4まで）がセーブに焼いたボタンの文言。窓口が、無害な spec で印の無いものを外す。
# 「やめておく」は入れない。ほかの MOD もゲームも同じ文言を出しうるので、こちらにしか無い文言だけで掃除する。
OUR_LABELS = (LEAVE_LABEL, CONFIRM_LABEL)
FAREWELL_TEXT = "{name}はパーティを離れ、{place}に残った。"
FAREWELL_TEXT_NO_PLACE = "{name}はパーティを離れた。"
NO_PLACE_TEXT = "……こんな場所で放り出すわけにはいかない。人の居る場所まで戻ろう。"
FAILED_TEXT = "（今は別れ話を切り出せない）"

# 会話を閉じるときにゲームへ渡す end_text。会話の要約とライフログに残る。
END_TEXT_TEMPLATE = "<行動: {name}と別れ、パーティから外れてもらった>"

# ---------------------------------------------------------------- 動作
# 別れた後に保存する。パーティの増減はセーブの game_variables にあり、
# 次の保存までに落ちると「別れたのに居る」状態で戻るため。
SAVE_AFTER_LEAVE = True

# 外した後、戦闘の立ち位置が残っていたら空に戻す（非パーティの NPC は null。GAME.md §2.8）。
CLEAR_BATTLE_POSITION = True

# 会話を閉じ終わるのを待つ間隔と上限（終了処理は要約で LLM を回すことがあるので長め）。
END_POLL = 0.3
END_TIMEOUT = 120.0

# 確認の画面の名前（窓口 `choices` の MOD だけの画面）。
CONFIRM_SCREEN = "confirm"

# 自前のボタンに付ける印。ほかの MOD がこのボタンを見分けるのに使う
# （`404_` はパーティー会話の間このキーで落とす）。`302_` から変えていない。
MARK = "mod_party_action"

BATTLE_FLAGS = ("in_battle", "in_colosseum_battle", "in_boss_battle")


def install(shared):
    ctx, cfg, write, screen = shared.ctx, shared.cfg, shared.write, shared.screen
    state = {
        "owner": None,         # 窓口 `choices` の持ち主の名前（`provide` が返す）
        "leaving": False,      # 二重実行よけ
        "last_skip": None,     # ボタンを出さなかった理由（ログの重複よけ）
    }

    def name_of(app, character_id):
        # 文言に混ぜるので、引けないときは id ではなく「その仲間」。
        return ui.character_name(app, character_id, fallback="その仲間")

    # ------------------------------------------------------- 出してよい場面か
    def blocking_reason(app):
        """仲間と別れられない場面なら理由を返す。相手が仲間かどうかは `is_member` が見る。"""
        for flag in BATTLE_FLAGS:
            if getattr(app, flag, False):
                return flag
        # 依頼の最中は名簿を触らない（`current_quest_data` は依頼の外では None）。
        if getattr(app, "current_quest_data", None):
            return "in a quest"
        return None

    def is_member(app, member_id):
        return bool(member_id) and member_id != "player" and member_id in ui.party_ids(app)

    def can_part(app, member_id):
        return is_member(app, member_id) and blocking_reason(app) is None

    # ============================================================ 自前のフェーズ
    class LeavePhase(object):
        """`app.process_choice` に渡してゲームと同じ経路を通すためのフェーズ。

        `PhaseSpec` には載せない（載せるとセーブに焼かれ、MOD 無しの次の起動で引けずに落ちる）。
        """

        def __init__(self, app, action, member_id):
            self.app = app
            self.action = action
            self.member_id = member_id

        def execute(self, choice_text):
            # ゲームの別スレッドで走る。例外をゲームの側へ抜けさせない。
            try:
                return dispatch(self.app, self.action, self.member_id)
            except Exception:
                ctx.log_exc("farewell: phase {!r} failed".format(self.action))
                return None

    def press(app, action, member_id, text):
        """自前のボタンが押された（窓口が押されたボタンの処理として呼ぶ）。"""
        write("farewell: pressed {!r} ({}) member={!r}".format(text, action, member_id))
        screen.start_phase(app, LeavePhase(app, action, member_id), text,
                           fallback=lambda: dispatch(app, action, member_id))
        return None

    def dispatch(app, action, member_id):
        if action == "confirm":
            ask_confirmation(app, member_id)
        elif action == "leave":
            begin_leave(app, member_id)
        elif action == "cancel":
            restore_buttons(app)

    def restore_buttons(app):
        """確認の画面を閉じて会話のボタンに戻す（会話のボタンは窓口が書き換えずに残している）。"""
        if not choices.close_screen(app, state["owner"]):
            choices.repaint(app)

    def refuse(app, step, member_id):
        write("farewell: {} refused ({})".format(step, blocking_reason(app) or "not a party member"))
        screen.say(app, FAILED_TEXT)

    # ============================================================ 確認を挟む
    def ask_confirmation(app, member_id):
        """押し間違いで仲間を失わせないため、本当に別れるかを訊く。"""
        if not can_part(app, member_id):
            refuse(app, "confirm", member_id)
            return
        choices.open_screen(app, state["owner"], CONFIRM_SCREEN, {"member": member_id})
        screen.say(app, CONFIRM_QUESTION.format(name=name_of(app, member_id)))
        write("farewell: asking about {!r} ({})".format(name_of(app, member_id), member_id))

    # ============================================================ 別れる
    def begin_leave(app, member_id):
        """確認が済んだ後。置き先を決め、会話を閉じてから外す。"""
        if state["leaving"]:
            return
        if not can_part(app, member_id):
            refuse(app, "leave", member_id)
            restore_buttons(app)
            return
        character = ui.character_of(app, member_id)
        if character is None:
            write("farewell: no character instance for {!r}".format(member_id))
            screen.say(app, FAILED_TEXT)
            restore_buttons(app)
            return
        facility, node, why = choose_destination(app, character)
        if facility is None:
            write("farewell: nowhere to leave {!r}; refusing".format(member_id))
            screen.say(app, NO_PLACE_TEXT)
            restore_buttons(app)
            return

        state["leaving"] = True
        # 「会話を終了する」は確認の画面の下のゲームの一覧に残っている（窓口は書き換えない）
        _partner, end_entry = ui.conversation_partner(choices.game_buttons(app))
        # 閉じに行く前に確認の画面を閉じる。閉じられなかったときは、組み直すと会話のボタンに戻る
        choices.close_screen(app, state["owner"], repaint_now=False)
        try:
            name = name_of(app, member_id)
            write("=" * 78)
            write("farewell: {!r} ({}) -> {!r} [{}] node={!r}".format(
                name, member_id, ui.facility_name(app, facility) or facility, why, node))

            def abort(_reason):
                screen.say(app, FAILED_TEXT)
                state["leaving"] = False
                # 会話が閉じなかった場合ゲームは組み直さないので、組み直して会話のボタンを塗る
                choices.repaint(app)

            screen.end_conversation(
                app, end_entry,
                lambda a: finish_leave(a, member_id, character, facility, node, name),
                end_text=END_TEXT_TEMPLATE.format(name=name),
                on_abort=abort, poll=END_POLL, timeout=END_TIMEOUT)
        except Exception:
            # 会話を閉じに行く前に投げた。「実行中」の印を戻さないと以後ずっと別れられなくなる
            ctx.log_exc("farewell: cannot start the farewell")
            state["leaving"] = False
            screen.say(app, FAILED_TEXT)
            choices.repaint(app)

    def choose_destination(app, character):
        """別れた仲間をどこへ置くか。`(施設, ノード, 理由)` を返す。

        近くに残す設定なら、雇った土地では雇った場所、土地を跨いでいたらいまの町のギルド。
        切ってあればゲームの置き先。引けなければ いまの町のギルド → ゲームの置き先 と下がる。
        """
        here = ui.current_area(app)
        here_id = ui.area_id_of(here)
        home_area, home_facility = home_of(character)
        if cfg.STAY_NEARBY and home_area and home_area == here_id:
            facility, node = ui.find_facility(here, home_facility)
            if facility is not None:
                return facility, node, "initial location"
            write("farewell: facility {!r} is not in area {!r} any more".format(home_facility, here_id))
        if not cfg.STAY_NEARBY:
            facility, node = game_destination(app, character)
            if facility is not None:
                return facility, node, "the game's own destination"
        if here is not None:
            facility, node = ui.find_guild(here, ui.GUILD_FACILITY_TYPE)
            if facility is not None:
                return facility, node, "guild of the current area"
        facility, node = game_destination(app, character)
        if facility is not None:
            return facility, node, "the game's own destination"
        return None, None, "nowhere"

    def game_destination(app, character):
        try:
            return unpack(app.get_party_leave_facility(character))
        except Exception:
            ctx.log_exc("farewell: get_party_leave_facility failed")
            return None, None

    def finish_leave(app, member_id, character, facility, node, name):
        """会話が閉じた後。ゲーム自身の処理でパーティから外し、置いて、保存する。"""
        try:
            record = {"moved": False}
            shared.departing[member_id] = record
            try:
                app.remove_party_member(member_id)
            finally:
                shared.departing.pop(member_id, None)

            if member_id in ui.party_ids(app):
                # remove_party_member が名簿を触らなかった。放っておくと「別れたのに居る」まま保存される
                write("farewell: WARN remove_party_member left {!r} in the party; removing by hand".format(member_id))
                for _label, store in ui.party_stores(app):
                    ui.drop_from_store(store, member_id)

            if record["moved"]:
                write("farewell: the game moved {!r} itself; not placing again".format(name))
            else:
                try:
                    app.move_npc_to_facility(member_id, character, facility, node)
                except Exception:
                    ctx.log_exc("farewell: move_npc_to_facility({!r}) failed".format(member_id))

            if CLEAR_BATTLE_POSITION and getattr(character, "display_position_in_battle", None) is not None:
                character.display_position_in_battle = None

            place = (ui.facility_name(app, facility)
                     or ui.facility_name(app, getattr(character, "current_location", None)))
            screen.say(app, FAREWELL_TEXT.format(name=name, place=place) if place
                       else FAREWELL_TEXT_NO_PLACE.format(name=name))

            if SAVE_AFTER_LEAVE:
                try:
                    app.save_game()
                except Exception:
                    ctx.log_exc("farewell: save_game failed")
            # 仲間欄は名簿を書き換えただけでは変わらない。別れの文が流れ終わってから塗り直す
            # （先に消えると、まだ別れていないうちに居なくなったように見える）
            screen.when_idle(app, lambda: screen.paint_party(app), proceed_on_timeout=True, tag="party ui")
            write("farewell: done ({!r}, party now {})".format(name, ui.party_ids(app)))
        except Exception:
            ctx.log_exc("farewell: finishing the farewell failed")
        finally:
            state["leaving"] = False

    # ================================================================ ボタン
    def offer(app, view):
        """会話の相手が仲間なら「ここで別れる」を出す（窓口 `choices` が組み直しのたびに呼ぶ）。"""
        if not cfg.FAREWELL_ENABLED:
            return None
        member_id = view.partner
        if not is_member(app, member_id):
            return None
        reason = blocking_reason(app)
        if reason is not None:
            if state["last_skip"] != (member_id, reason):
                state["last_skip"] = (member_id, reason)
                write("farewell: not offering to {!r}: {}".format(name_of(app, member_id), reason))
            return None
        return [choices.Choice(LEAVE_LABEL, place=choices.before("ConversationEndManager"),
                               marks={MARK: "confirm"},
                               on_press=lambda app: press(app, "confirm", member_id, LEAVE_LABEL))]

    def confirm_screen(app, view, data):
        """確認の画面。その仲間との会話でなくなったら閉じる（None）。"""
        member_id = (data or {}).get("member")
        if not member_id or view.partner != member_id or not can_part(app, member_id):
            write("farewell: confirm closed ({!r} is no longer the party member in this talk)".format(member_id))
            return None
        return [choices.Choice(CONFIRM_LABEL, on_press=lambda app: press(app, "leave", member_id, CONFIRM_LABEL)),
                choices.Choice(CANCEL_LABEL, on_press=lambda app: press(app, "cancel", member_id, CANCEL_LABEL))]

    state["owner"] = choices.provide(ctx, screen, offer=offer, screens={CONFIRM_SCREEN: confirm_screen},
                                     labels=OUR_LABELS)
