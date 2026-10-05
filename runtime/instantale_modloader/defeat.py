# -*- coding: utf-8 -*-
"""倒れた戦闘を、死なせずに逃走と同じ終わり方で終える窓口。

ゲームは主人公が倒れると `BattlePhaseManager.check_battle_end` の中で `GameOverManager` を作る
（ゲームオーバーでセーブが消える。GAME.md §2.10）。
闘技場の負け（`334_colosseum_custom`）や脱獄の決行（`336_crime_overhaul`）のように、
倒れても話を続けたい戦闘を持つ MOD がここに登録する。

    defeat.install(ctx)                   # apply() の中。何本の MOD が呼んでも1つの世代に1回だけ包む
    defeat.declare(owner, ctx, decide)    # decide(app) -> None（この戦闘は知らない）/ dict
    defeat.surrendering()                 # 切り上げの終わり方を起こしている最中か（その中の end_phase から見る）

主人公の体力が 0 以下で `check_battle_end` が来たら、登録の `decide(app)` を順に聞く。
最初に dict を返した登録の戦闘として、次の順で切り上げる（どれも `334_` の実機で通した手順）。

    1. 体力を `hp`（既定 1）にする。`text` があれば本文に1行出す
    2. ゲーム自身の逃走と同じ状態にする（敵の一覧を空にし、一覧から外された主人公を預かりへ入れる。
       戻すのはゲームの終わり方に任せる。`233_probe_colosseum` 版3 の比較）
    3. `BattleEndManager(app, 'escaped')` を起こす（ゲームが逃げたときと同じマネージャと引数）
    4. ゲームが主人公を一覧へ戻さなかったときだけ、控えた値で戻す
    5. `then(app)` を呼ぶ

切り上げた戦闘の残りの手（`handle_battle_situation` / `reduce_status_turns_and_log`）は飛ばす
（一覧から消えた敵を引いて `KeyError` でワーカースレッドが死んだ）。
切り上げた直後の敵の欄の更新の 0 除算（`new_hud.py:2306`）は、10 秒の間だけ握る（戦闘の欄が畳まれた後に走った）。
倒れた仲間は扱わない（一覧へ戻すかは MOD が決める。`334_` の `hand_over_fallen`）。
逃走の終わり方なので、ゲームは衛兵戦と同じくその土地の手配度を 10 下げる（GAME.md §2.20。戻すかは MOD が決める）。

登録は持ち主ごとに1つ。登録した ctx が用済みなら数えない。`decide` / `then` が投げたら、
その登録は口を出さなかったものとみなす（`wanted` と同じ。`errors` に残す）。
"""
import sys
import time

from . import log, log_exc, ui

END_MANAGER_CLS = "BattleEndManager"
ESCAPED_END_TYPE = "escaped"
PLAYER_KEY = "player"
SURVIVE_HP = 1
#: 切り上げた `BattlePhaseManager` の実体に付ける印。
SURRENDERED_MARK = "_instantale_defeat_surrendered"
#: 切り上げた後に飛ばす手。
AFTER_SURRENDER_STEPS = ("handle_battle_situation", "reduce_status_turns_and_log")
CHECK_TARGET = "__main__:BattlePhaseManager.check_battle_end"
REMOVE_TARGET = "__main__:InstantaleApp.remove_party_member"
ENEMY_DISPLAY_TARGET = "scripts.hud.new_hud:InstanTaleHUD.update_enemy_display"
SURRENDER_GUARD_SECONDS = 10

_STATE_ATTR = "_instantale_defeat"
_GATE_ATTR = "_instantale_defeat_gate"


def _state():
    state = getattr(sys, _STATE_ATTR, None)
    if not isinstance(state, dict):
        state = {"owners": {}, "errors": [], "removed_player": None, "surrendering": False,
                 "surrendered_at": None, }
        setattr(sys, _STATE_ATTR, state)
    return state


def _write(text):
    """窓口の記録は `modloader.log` へ（どの MOD が先に包んだかで行き先を変えない）。"""
    try:
        log(text, level="WARN" if text.startswith("WARN") else "INFO")
    except Exception:
        pass


def _error(owner, exc):
    errors = _state()["errors"]
    errors.append("{}: {}: {}".format(owner, type(exc).__name__, exc))
    del errors[:-20]


def _alive(ctx):
    superseded = getattr(ctx, "superseded", None)
    if not callable(superseded):
        return True
    try:
        return not superseded()
    except Exception:
        return True


def declare(owner, ctx, decide):
    """倒れた戦闘を引き受けるかを決める関数を登録する。同じ持ち主の登録は差し替える。"""
    _state()["owners"][str(owner)] = (ctx, decide)


def withdraw(owner):
    _state()["owners"].pop(str(owner), None)


def surrendering():
    """切り上げの終わり方を起こしている最中か。"""
    return bool(_state()["surrendering"])


def fallen(app):
    """主人公の体力が 0 以下か。"""
    hp = getattr(getattr(app, "player", None), "current_hp", None)
    return isinstance(hp, (int, float)) and not isinstance(hp, bool) and hp <= 0


def _claim(app):
    """`(持ち主, 答え)`。どの登録も引き受けなければ `(None, None)`。"""
    for owner, (ctx, decide) in list(_state()["owners"].items()):
        if not _alive(ctx):
            continue
        try:
            answer = decide(app)
        except Exception as exc:
            _error(owner, exc)
            continue
        if isinstance(answer, dict):
            return owner, answer
    return None, None


def _prepare_escape(app):
    """ゲーム自身の逃走と同じ状態にする（GAME.md §2.10「逃げたときと倒れたとき」）。"""
    state = _state()
    enemies = getattr(app, "current_enemy_dict", None)
    if isinstance(enemies, dict) and enemies:
        names = list(enemies)
        enemies.clear()
        _write("defeat: cleared {} enem{} like the game's own escape ({})".format(
            len(names), "y" if len(names) == 1 else "ies", ", ".join(names)))
    party = getattr(app, "party", None)
    if isinstance(party, dict) and PLAYER_KEY in party:
        return                              # まだ一覧に居る（外されていない）
    removed = state["removed_player"]
    escaped = getattr(app, "escaped_member_in_battle", None)
    if removed is not None and isinstance(escaped, dict):
        escaped[PLAYER_KEY] = removed
        _write("defeat: handed the player to escaped_member_in_battle for the game to bring back")
    else:
        _write("WARN defeat: the player left the party and cannot be handed back "
               "(kept {}, escaped {})".format(type(removed).__name__, type(escaped).__name__))


def _ensure_player_back(app):
    """ゲームが主人公を一覧へ戻さなかったときだけ、控えた値で戻す。"""
    state = _state()
    party = getattr(app, "party", None)
    removed, state["removed_player"] = state["removed_player"], None
    if not isinstance(party, dict):
        return
    if PLAYER_KEY in party:
        _write("defeat: the player is back in the party")
        return
    if removed is None:
        _write("WARN defeat: the player is not in the party and cannot be put back")
        return
    party[PLAYER_KEY] = removed
    escaped = getattr(app, "escaped_member_in_battle", None)
    if isinstance(escaped, dict):
        escaped.pop(PLAYER_KEY, None)
    _write("WARN defeat: the game did not bring the player back; put back by hand")


def _surrender(app, owner, answer):
    """逃走と同じ終わり方を起こす。起こせたら True。"""
    cls = ui.cls_of(END_MANAGER_CLS)
    if cls is None:
        _write("WARN defeat: {} is not available".format(END_MANAGER_CLS))
        return False
    try:
        manager = cls(app, ESCAPED_END_TYPE)
    except Exception:
        log_exc("defeat: cannot build the battle end manager")
        return False
    state = _state()
    # 落ちても何をしようとしていたかが残るよう、起こす前に書く。
    _write("defeat: ending the battle as an escape for {}".format(owner))
    state["surrendered_at"] = time.monotonic()
    try:
        _prepare_escape(app)
    except Exception:
        log_exc("defeat: cannot prepare the escape")
    state["surrendering"] = True
    try:
        manager.execute("")
    except Exception:
        log_exc("defeat: the escape ending failed")
        return False
    finally:
        state["surrendering"] = False
    try:
        _ensure_player_back(app)
    except Exception:
        log_exc("defeat: cannot put the player back in the party")
    return True


def gate():
    return getattr(sys, _GATE_ATTR, None)


def install(ctx):
    """倒れた判定・一覧から外す・残りの手・敵の欄の更新を包む。包んだ対象の名前を返す。

    何本の MOD が呼んでも、1つの世代につき1回だけ（`durations.install` と同じ形。TECH.md §5.8）。
    """
    generation = getattr(ctx, "generation", None)
    done = gate()
    if done is not None and done.get("generation") == generation:
        return list(done.get("targets") or [])

    def check_battle_end(orig, self, *args, **kwargs):
        """倒れていて引き受ける登録があれば、ゲームの判定（ゲームオーバーを作る）より先に切り上げる。"""
        if getattr(self, SURRENDERED_MARK, False):
            return True                     # 切り上げた戦闘。もう判定しない
        try:
            app = getattr(self, "app", None) or ui.find_app()
            if fallen(app):
                owner, answer = _claim(app)
                if answer is not None:
                    player = getattr(app, "player", None)
                    hp = player.current_hp
                    player.current_hp = answer.get("hp") or SURVIVE_HP
                    text = answer.get("text")
                    if text:
                        try:
                            app.add_text(text)
                        except Exception:
                            log_exc("defeat: add_text failed")
                    if _surrender(app, owner, answer):
                        setattr(self, SURRENDERED_MARK, True)
                        _write("defeat: hp {} -> {}; ended the battle as an escape for {}".format(
                            hp, player.current_hp, owner))
                        then = answer.get("then")
                        if callable(then):
                            try:
                                then(app)
                            except Exception as exc:
                                _error(owner, exc)
                                log_exc("defeat: {} failed after the surrender".format(owner))
                        return True
                    _write("WARN defeat: hp {} -> {} but the battle goes on".format(
                        hp, player.current_hp))
        except Exception:
            log_exc("defeat: cannot end the battle as a loss")
        return orig(self, *args, **kwargs)

    def remove_party_member(orig, self, member_id=None, *args, **kwargs):
        """戦闘の中で一覧から外される主人公の値を控える（切り上げで一覧へ戻すため）。"""
        try:
            if member_id == PLAYER_KEY and getattr(self, "in_battle", False):
                party = getattr(self, "party", None)
                if isinstance(party, dict) and member_id in party:
                    _state()["removed_player"] = party[member_id]
        except Exception:
            log_exc("defeat: cannot keep the player leaving the party")
        return orig(self, member_id, *args, **kwargs)

    def install_after_surrender(name):
        def after_surrender(orig, self, *args, **kwargs):
            if getattr(self, SURRENDERED_MARK, False):
                _write("defeat: skipped {} after the battle ended".format(name))
                return None
            return orig(self, *args, **kwargs)

        target = "__main__:BattlePhaseManager.{}".format(name)
        ctx.wrap(target, required=False)(after_surrender)
        return target

    def enemy_display(orig, *args, **kwargs):
        try:
            return orig(*args, **kwargs)
        except ZeroDivisionError:
            since = _state()["surrendered_at"]
            if since is None or time.monotonic() - since > SURRENDER_GUARD_SECONDS:
                raise
            _write("defeat: skipped an enemy panel update after the battle ended "
                   "(ZeroDivisionError)")
            return None

    ctx.wrap(CHECK_TARGET, required=False, safe=True)(check_battle_end)
    ctx.wrap(REMOVE_TARGET, required=False, safe=True)(remove_party_member)
    targets = [CHECK_TARGET, REMOVE_TARGET]
    targets += [install_after_surrender(name) for name in AFTER_SURRENDER_STEPS]
    ctx.wrap(ENEMY_DISPLAY_TARGET, required=False)(enemy_display)
    targets.append(ENEMY_DISPLAY_TARGET)
    setattr(sys, _GATE_ATTR, {"generation": generation, "targets": targets})
    _write("defeat: wrapped {}".format(", ".join(targets)))
    return targets


def reset():
    """全部忘れる（検査用）。"""
    setattr(sys, _STATE_ATTR, None)
    setattr(sys, _GATE_ATTR, None)
