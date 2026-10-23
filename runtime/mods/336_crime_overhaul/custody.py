# -*- coding: utf-8 -*-
"""身柄の拘束。捕まったら仲間と引き離し、牢に居る間は閉じ込めていると知らせる。仕様は DOC.md「捕まったとき」。

素のゲームは、捕まっても仲間をパーティから外さない。裁判の画面にも牢の中にも、処刑場からの脱出にも仲間が付いてきた（2026-10-21）。

- 裁判の始まり（`TrialStartManager.execute`。衛兵に捕まる経路はどれもここを通る。GAME.md §2.20）の前に、
  仲間をゲーム自身の手順で解散させる（`remove_party_member` → 雇われた場所 `get_party_leave_facility` へ置き直す。
  GAME.md §2.4 の「外す処理は書かない」）。牢を出ても戻らない。会いに行って雇い直す
- 服役の始まり（`prison` の `ImprisonmentStartManager.execute` の包み）と処刑場からの脱出の始まり（`rescue`）から、牢を出るまで
  （`prison_event("exit")`）は、ローダの窓口 `confinement` に閉じ込めていると返す（返すのは `cellmate` の登録）。
  ロードをまたいでも続くよう、世界×主人公の控え（`custody`）に持つ
"""
from instantale_modloader import ui

TRIAL_TARGET = "__main__:TrialStartManager.execute"
PARTED_TEXT = "{names}とは引き離され、パーティは解散した。"
#: MOD の人物の id の頭。置き先をゲームが引けないので外さない。
MOD_NPC_PREFIX = "mod:"


def install(env):
    ctx, write, screen, worlds, cfg = env.ctx, env.write, env.screen, env.worlds, env.cfg

    def in_custody(app):
        """牢に居る（服役中・処刑場からの脱出の最中）か。"""
        with worlds.lock:
            return bool(worlds.load(worlds.playthrough(app)).get("custody"))

    def set_custody(app, value, why):
        playthrough = worlds.playthrough(app)
        with worlds.lock:
            bucket = worlds.load(playthrough)
            if bool(bucket.get("custody")) == bool(value):
                return
            if value:
                bucket["custody"] = True
            else:
                bucket.pop("custody", None)     # 牢の外では何も残さない
            worlds.save(playthrough)
        write("custody: {} ({})".format("in" if value else "out", why))

    env.in_custody = in_custody
    env.set_custody = set_custody

    def send_back(app, member_id):
        """仲間1人をゲーム自身の手順で外し、雇われた場所へ置く。外した名前を返す（外せなければ None）。塗り直さない。"""
        remove = getattr(app, "remove_party_member", None)
        leave_to = getattr(app, "get_party_leave_facility", None)
        mover = getattr(app, "move_npc_to_facility", None)
        if not (callable(remove) and callable(leave_to) and callable(mover)):
            write("WARN custody: the game's party removal is not available")
            return None
        character = ui.character_of(app, member_id)
        if character is None or str(member_id).startswith(MOD_NPC_PREFIX):
            write("custody: kept {} in the party (no place to send)".format(member_id))
            return None
        try:
            found = leave_to(character)
        except Exception:
            ctx.log_exc("crime incentive: get_party_leave_facility failed for {}".format(member_id))
            return None
        # タプル `(施設, ノード)` で返る（そのまま渡すと落ちる。GAME.md §2.4）
        facility, node = found if isinstance(found, tuple) and len(found) == 2 else (found, None)
        if facility is None:
            write("custody: kept {} in the party (no facility to return to)".format(member_id))
            return None         # 置き先を決めずに外さない（世界のどこにも居なくなる）
        remove(member_id)
        try:
            mover(member_id, character, facility, node)
        except Exception:
            ctx.log_exc("crime incentive: move_npc_to_facility failed for {}".format(member_id))
        write("custody: {} left the party for {!r}".format(member_id, getattr(facility, "name", facility)))
        return getattr(character, "name", None) or str(member_id)

    env.send_back = send_back

    def disband(app):
        """仲間を解散させる。外した名前を返す。"""
        names = [name for name in (send_back(app, member_id) for member_id in list(ui.party_member_ids(app)))
                 if name is not None]
        if names:
            screen.schedule(lambda: screen.paint_party(app))     # 裁判の段はワーカースレッド。塗るのはメインスレッド（`rescue` と同じ）
        return names

    @ctx.wrap(TRIAL_TARGET, required=False, safe=True)
    def trial_start(orig, self, *args, **kwargs):
        """裁判の前に、仲間と引き離す。"""
        try:
            app = getattr(self, "app", None) or ui.find_app()
            if app is not None and cfg.CUSTODY_DISBAND:
                names = disband(app)
                if names:
                    screen.say(app, PARTED_TEXT.format(names="・".join(names)))
        except Exception:
            ctx.log_exc("crime incentive: cannot part from the party")
        return orig(self, *args, **kwargs)

    def on_exit(app, how=""):
        set_custody(app, False, how or "exit")

    env.on_prison("exit", on_exit)
