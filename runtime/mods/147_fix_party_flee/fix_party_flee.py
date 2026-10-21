# -*- coding: utf-8 -*-
"""修正: 主人公が逃げると、仲間も一緒に戦闘から逃げる。

## 原因（GAME.md §2.10「逃げたときと倒れたとき」）

ゲームの「逃げる」は、逃げた者を `app.party` から外して `app.escaped_member_in_battle` に預ける
（`resolve_battle_effect` の `remove_party_member('player')`）。
`check_battle_end` は一覧が空になったときに逃走で終え（敵の一覧を空にして `BattleEndManager(app, 'escaped')`）、
その終わり方の中で預かりを全員一覧へ戻す。

主人公1人（闘技場）なら外した時点で一覧が空になって終わる。
仲間がいると一覧が空にならないので、主人公だけが抜けて仲間が戦い続け、
画面には主人公の手番のボタンが並んだまま残った（2026-10-21 の実機。VERIFICATION.md §3.110）。

## 直し方

主人公が逃げた手（`resolve_battle_effect` で主人公が新しく預かりに入った手）の直後に、
一覧に残った仲間も預かりへ移す。主人公1人で逃げたときと同じ状態になるので、
終わらせるのも全員を一覧へ戻すのもゲーム自身。
預かりに入れた仲間をゲームが戻すことは `334_colosseum_custom` の実機で見えている
（倒れた仲間を預けた回。`fallen: escaped: … is back in the party`）。

逃げる手で敵も倒し切ると、判定は先に全滅を見て勝ちの終わり方へ進み、預かりを戻さない（GAME.md §2.10）。
`107_fix_battle_flag_stuck` が戻すが、入っていなくても仲間が消えないよう、
戦闘の終わり方の後で、ここで移した仲間が一覧に居なければ手で戻す。

審判の文は主人公の逃走しか書かないので、その手の地の文の後に1行足す（`FOLLOW_TEXT`）。
"""

import threading

from instantale_modloader import ui

LOG_BASENAME = "party_flee.log"

PLAYER_KEY = "player"
#: 仲間が後に続いたときの本文。{names} は仲間の名前（「と」と「、」でつなぐ）。
FOLLOW_TEXT = "{names}も後に続いて戦いを離れた。"

RESOLVE_TARGET = "__main__:BattlePhaseManager.resolve_battle_effect"
#: 1手ぶん。中で `resolve_battle_effect` → 地の文と回る（`308_battle_damage_display` の記録）。
HANDLE_TARGET = "__main__:BattlePhaseManager.handle_battle_situation"
CHECK_TARGET = "__main__:BattlePhaseManager.check_battle_end"
END_TARGETS = (
    ("__main__:BattleEndManager.end_phase", "BattleEndManager.end_phase"),
    ("__main__:BattleEndInFreeAction.end_phase", "BattleEndInFreeAction.end_phase"),
    ("__main__:BattleEndInColosseum.end_phase", "BattleEndInColosseum.end_phase"),
)


def join_names(names):
    """「A」「AとB」「A、B、C」。"""
    names = [name for name in names if name]
    if len(names) <= 1:
        return "".join(names)
    if len(names) == 2:
        return "{}と{}".format(*names)
    return "、".join(names)


def follow(party, escaped):
    """主人公が預かりに入っていれば、一覧に残った仲間も預かりへ移す。移した `[(id, 値)]` を返す。"""
    if not isinstance(party, dict) or not isinstance(escaped, dict):
        return []
    if PLAYER_KEY not in escaped or PLAYER_KEY in party:
        return []
    moved = []
    for member_id, value in list(party.items()):
        escaped[member_id] = value
        del party[member_id]
        moved.append((member_id, value))
    return moved


def apply(ctx):
    write = ctx.logger(LOG_BASENAME, tag="party flee:")
    lock = threading.Lock()
    # 移した仲間（戦闘の終わりまで）と、まだ出していない本文の1行。
    state = {"moved": [], "line": None}

    def name_of(app, member_id, value):
        name = getattr(value, "name", None)
        if isinstance(name, str) and name:
            return name
        return ui.character_name(app, member_id, fallback=str(member_id))

    def say_pending(app):
        with lock:
            line, state["line"] = state["line"], None
        if not line or app is None:
            return
        try:
            app.add_text(line)
        except Exception:
            ctx.log_exc("party flee: add_text failed")

    @ctx.wrap(RESOLVE_TARGET, required=False)
    def resolve_battle_effect(orig, self, *args, **kwargs):
        app = getattr(self, "app", None) or ui.find_app()
        escaped = getattr(app, "escaped_member_in_battle", None)
        was_out = isinstance(escaped, dict) and PLAYER_KEY in escaped
        result = orig(self, *args, **kwargs)
        if was_out or app is None:
            return result
        try:
            moved = follow(getattr(app, "party", None), getattr(app, "escaped_member_in_battle", None))
            if moved:
                names = [name_of(app, member_id, value) for member_id, value in moved]
                with lock:
                    state["moved"].extend(moved)
                    state["line"] = FOLLOW_TEXT.format(names=join_names(names))
                write("the player fled; {} followed into escaped_member_in_battle".format(
                    ", ".join("{} ({})".format(member_id, name)
                              for (member_id, _value), name in zip(moved, names))))
        except Exception:
            ctx.log_exc("party flee: cannot move the party after the player")
        return result

    @ctx.wrap(HANDLE_TARGET, required=False)
    def handle_battle_situation(orig, self, *args, **kwargs):
        result = orig(self, *args, **kwargs)
        say_pending(getattr(self, "app", None) or ui.find_app())
        return result

    @ctx.wrap(CHECK_TARGET, required=False)
    def check_battle_end(orig, self, *args, **kwargs):
        # 1手ぶんの包みを通らない経路でも、終わり方の本文より先に出す
        say_pending(getattr(self, "app", None) or ui.find_app())
        return orig(self, *args, **kwargs)

    def ensure_back(app, where):
        """ここで移した仲間が一覧に居なければ戻す（勝ちの終わり方はゲームが預かりを戻さない）。"""
        with lock:
            moved, state["moved"] = state["moved"], []
            state["line"] = None
        if not moved or app is None:
            return
        party = getattr(app, "party", None)
        escaped = getattr(app, "escaped_member_in_battle", None)
        if not isinstance(party, dict):
            write("WARN {}: the party is {}; cannot check {}".format(
                where, type(party).__name__, [member_id for member_id, _v in moved]))
            return
        back = []
        for member_id, value in moved:
            if member_id in party:
                continue
            party[member_id] = value
            if isinstance(escaped, dict):
                escaped.pop(member_id, None)
            back.append(member_id)
        if back:
            write("{}: the game did not bring {} back; put back by hand".format(where, back))
        else:
            write("{}: the party is back ({})".format(where, [member_id for member_id, _v in moved]))

    def on_end(target, label):
        @ctx.wrap(target, required=False)
        def _end(orig, self, *args, **kwargs):
            result = orig(self, *args, **kwargs)
            try:
                ensure_back(getattr(self, "app", None) or ui.find_app(), label)
            except Exception:
                ctx.log_exc("party flee: cannot bring the party back")
            return result
        return _end

    for _target, _label in END_TARGETS:
        on_end(_target, _label)

    ctx.log("party flee: the party follows when the player flees ({})".format(ctx.out_path(LOG_BASENAME)))
