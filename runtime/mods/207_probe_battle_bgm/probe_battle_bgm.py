# -*- coding: utf-8 -*-
"""計測: 戦闘 BGM の切り替え経路を特定する（読み取り専用）。

症状: **会話から戦闘に入ると、戦闘終了後も戦闘 BGM が流れ続ける**。
通常の戦闘（クエスト中のエンカウント等）では戻っている。

BGM を鳴らす口はプロセス内に1つしかない。

    scripts.sounds:SoundManager.play_music_from_src(self, app, music_src)
    scripts.sounds:SoundManager.stop_music(self, app)

したがって「戦闘後に元の曲へ戻す呼び出しが無い」のか、
「呼ばれてはいるが効いていない」のかは、この2つを包めば必ず分かる。
後者の可能性が実際にあり、`app` には
`music` という属性がある（`205_` のスナップショットで確認済み）。
これが「今鳴っている曲」の控えで、同じ src なら鳴らし直さない、
という作りなら、**戦闘曲を鳴らすときに
`app.music` を更新し忘れている**だけで症状が説明できる。
そこで前後の `app.music` を必ず記録する。

呼び出し元は `frames.caller` で取る（段数では数えない。TECH.md §6.3）。
Nuitka でコンパイルされていてもフレームは通常どおり積まれる（crash_log.txt が
`instantale.py:4033` の形で行番号まで出しているのがその証拠）。
どの戦闘終了マネージャが曲を戻していて、会話経由ではどれが走っていないのかが、
そのまま読める。

この mod は観測しかしない。
値は変えず、記録に失敗しても本体は必ず呼ぶ。
"""

import os
import sys
import threading

from instantale_modloader import frames, ui
from instantale_modloader.frames import repr_value

LOG_BASENAME = "battle_bgm.log"

# 起動直後に一度だけ、音まわりの今の姿を写し取る。
SNAPSHOT_ON_BOOT = True

# 呼び出し元として記録するフレーム数（自分のラッパを除いた直近ぶん）。
STACK_DEPTH = 8

# BGM の呼び出しは多くないので、上限を緩くしてよい。
# 戦闘1回ぶんを丸ごと残したい。
MAX_SAMPLES = 200

# 戦闘まわりの状態フラグ。
# 曲が切り替わった瞬間にどれが立っていたかを見る。
BATTLE_FLAGS = ("in_battle", "in_boss_battle", "in_colosseum_battle",
                "in_conversation", "in_free_input", "in_action_in_conversation")


def apply(ctx):
    log_path = ctx.out_path(LOG_BASENAME)
    counts = {}

    write = ctx.logger(LOG_BASENAME)

    def sample(key):
        n = counts.get(key, 0)
        counts[key] = n + 1
        return n < MAX_SAMPLES

    find_app = ui.find_app     # 走っている app の探し方はローダの語彙

    def short_src(value):
        """曲のパスは長いので、末尾2階層だけにして読めるようにする。"""
        if not isinstance(value, str) or not value:
            return repr(value)
        parts = value.replace("\\", "/").rstrip("/").split("/")
        return "/".join(parts[-3:]) if len(parts) >= 3 else value

    def flags_of(app):
        if app is None:
            return "<no app>"
        return " ".join("{}={}".format(name, 1 if getattr(app, name, False) else 0)
                        for name in BATTLE_FLAGS)

    def who(obj):
        """渡されたオブジェクトが本物の app かどうか。

        戦闘終了の復帰呼び出しだけ `app.music` が
        `<missing>` になっていたので、**app ではない何かが渡されている**という読みを確定させるために足した。
        型と id が分かれば、使い回しなのか毎回作り直されているのかも分かる。
        """
        try:
            return "{}#{:x}{}".format(type(obj).__name__, id(obj),
                                      " IS-APP" if obj is find_app() else " NOT-THE-APP")
        except Exception:
            return "<unknown>"

    def channels():
        """今いくつのチャンネルが鳴っているか ＝ 曲が重なっていないかの真実。

        `app.music` は最後に鳴らした曲しか指さないので、
        迷子になった曲はそこからは見えない。
        pygame に直接聞けば全部数えられる。
        """
        try:
            import pygame
            total = pygame.mixer.get_num_channels()
            busy = [i for i in range(total) if pygame.mixer.Channel(i).get_busy()]
            return "{}/{} channel(s) busy: {}".format(len(busy), total, busy)
        except Exception as exc:
            return "<mixer unavailable: {}>".format(type(exc).__name__)

    def sound_state(sound):
        try:
            return "{}#{:x} playing_on={}".format(
                type(sound).__name__, id(sound), sound.get_num_channels())
        except Exception:
            return repr(sound)

    def callers():
        """自分のラッパより手前のフレームを並べる。

        段数で数えないこと（TECH.md §6.3）。
        この MOD は `BattleEndManager.execute` なども包んでいるので、
        そこから
        `play_music_from_src` に到達した呼び出しでは自分のラッパがスタックに載る。
        以前は末尾2段の決め打ちで落としていて、
        ファイル名での除外（`startswith("207_probe")`）は**実際のファイル名が
        `probe_battle_bgm.py`** なので一度も一致していなかった。
        つまり効いていたのは段数の決め打ちだけだった。

        `frames.caller` はローダと MOD のフレームを置き場所で飛ばすので、
        何段挟まっても正しい呼び出し元から並ぶ。
        """
        return frames.caller(depth=STACK_DEPTH)

    # ------------------------------------------------------- 起動時の一発計測
    def snapshot():
        write("=" * 78)
        write("battle bgm snapshot (pid {})".format(os.getpid()))
        app = find_app()
        if app is None:
            write("  InstantaleApp instance not found (not in a game yet?)")
            return
        # 「今鳴っている曲」の控えがどこにあるか。
        # app.music が本命。
        for attr in ("music", "sound", "sound_manager"):
            value = getattr(app, attr, "<missing>")
            write("  app.{:<14} = [{}] {}".format(
                attr, type(value).__name__, repr_value(value)))
            # SoundManager のインスタンス変数（再生状態の控えがあるはず）。
            if attr in ("sound_manager", "sound") and not isinstance(value, (str, type(None))):
                try:
                    for key, val in sorted(vars(value).items()):
                        write("      .{:<20} = [{}] {}".format(
                            key, type(val).__name__, repr_value(val)))
                except Exception as exc:
                    write("      <vars() unavailable: {}>".format(type(exc).__name__))

        write("  mixer          = {}".format(channels()))
        write("  app.music      = {}".format(sound_state(getattr(app, "music", None))))
        player = getattr(app, "player", None)
        area = getattr(player, "current_area", None)
        write("  current area   = {!r} (id={!r}) size={!r}".format(
            getattr(area, "name", None), getattr(area, "id", None),
            getattr(area, "size", None)))
        write("  area.bgm       = {!r}".format(getattr(area, "bgm", "<missing>")))
        write("  flags          = {}".format(flags_of(app)))
        # フラグが当てにならないので、戦闘の実体があるかどうかも見る。
        # 敵が居なければ、立っている `in_battle` は残骸。
        for attr in ("current_enemy_dict", "combat_log", "loot_container"):
            write("  app.{:<14} = {}".format(attr, repr_value(getattr(app, attr, None))))

        # SoundManager クラスの実像。
        # 差し替え先や引数の形の確認用。
        sounds = sys.modules.get("scripts.sounds")
        cls = getattr(sounds, "SoundManager", None) if sounds is not None else None
        if isinstance(cls, type):
            write("  SoundManager methods = {}".format(
                sorted(n for n in vars(cls) if not n.startswith("__"))))

    if SNAPSHOT_ON_BOOT:
        try:
            snapshot()
        except Exception:
            ctx.log_exc("battle bgm probe: snapshot failed")

    # ==================================================================
    # 1. 曲を鳴らす/止める口。
    # ここが全ての判定材料。
    # ==================================================================
    @ctx.wrap("scripts.sounds:SoundManager.play_music_from_src", required=False)
    def play_music_from_src(orig, self, app, music_src, *args, **kwargs):
        before = getattr(app, "music", "<missing>")
        if sample("play_music_from_src"):
            write("-" * 78)
            write("play_music_from_src({!r})".format(short_src(music_src)))
            write("    target = {}".format(who(app)))
            write("    .music before = {!r}".format(short_src(before)))
            write("    flags  = {}".format(flags_of(app)))
            write("    caller = {}".format(callers()))
            write("    thread = {}".format(threading.current_thread().name))
        result = orig(self, app, music_src, *args, **kwargs)
        if counts.get("play_music_from_src", 0) <= MAX_SAMPLES:
            write("    .music after  = {}".format(
                sound_state(getattr(app, "music", None))))
            write("    mixer  = {}".format(channels()))
        return result

    @ctx.wrap("scripts.sounds:SoundManager.stop_music", required=False)
    def stop_music(orig, self, app, *args, **kwargs):
        if sample("stop_music"):
            write("stop_music() target={} .music={}".format(
                who(app), sound_state(getattr(app, "music", None))))
            write("    flags  = {}".format(flags_of(app)))
            write("    caller = {}".format(callers()))
        result = orig(self, app, *args, **kwargs)
        if counts.get("stop_music", 0) <= MAX_SAMPLES:
            write("    mixer after stop = {}".format(channels()))
        return result

    # ==================================================================
    # 2. 戦闘の開始と終了。
    # どの経路がどのマネージャを通るか。
    # ==================================================================
    def trace(target, label, with_music=True):
        """入口と出口で状態を書くだけの汎用ラッパ。"""
        @ctx.wrap(target, required=False)
        def _traced(orig, self, *args, **kwargs):
            app = getattr(self, "app", None) or find_app()
            if sample(target):
                write("=" * 78)
                write("{}({}) start".format(label, repr_value(args)[:200]))
                if with_music:
                    write("    app.music = {!r} | {}".format(
                        short_src(getattr(app, "music", "<missing>")), flags_of(app)))
                write("    thread = {} caller = {}".format(
                    threading.current_thread().name, callers()))
            result = orig(self, *args, **kwargs)
            if counts.get(target, 0) <= MAX_SAMPLES:
                write("{} done".format(label))
                if with_music:
                    write("    app.music = {!r} | {}".format(
                        short_src(getattr(app, "music", "<missing>")), flags_of(app)))
            return result
        return _traced

    # 開始側。
    # 会話からの戦闘とそれ以外で、どこが分岐するか。
    trace("__main__:InstantaleApp.start_battle_with_in_conversation",
          "InstantaleApp.start_battle_with_in_conversation")
    trace("__main__:InstantaleApp.execute_battle_process",
          "InstantaleApp.execute_battle_process")
    trace("__main__:BattleStartManager.start_battle", "BattleStartManager.start_battle")

    # 終了側。
    # 3種類ある。
    # 会話経由でどれが走る（走らない）のかがこの計測の核心。
    trace("__main__:BattlePhaseManager.check_battle_end",
          "BattlePhaseManager.check_battle_end")
    trace("__main__:BattleEndManager.end_phase", "BattleEndManager.end_phase")
    trace("__main__:BattleEndManager.execute", "BattleEndManager.execute")
    trace("__main__:BattleEndInFreeAction.end_phase", "BattleEndInFreeAction.end_phase")
    trace("__main__:BattleEndInColosseum.end_phase", "BattleEndInColosseum.end_phase")
    trace("__main__:LootPhaseManager.looting_phase", "LootPhaseManager.looting_phase")

    # 戦闘終了マネージャがどの end_type で作られるか。
    # 分岐の語彙が分かる。
    @ctx.wrap("__main__:BattleEndManager.__init__", required=False)
    def battle_end_init(orig, self, app, end_type, *args, **kwargs):
        write("BattleEndManager(end_type={!r}) flags={}".format(end_type, flags_of(app)))
        return orig(self, app, end_type, *args, **kwargs)

    ctx.log("battle bgm probe log: {}".format(log_path))
