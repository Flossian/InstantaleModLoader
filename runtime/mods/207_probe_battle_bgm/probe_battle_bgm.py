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

版3: 原因は決着し（VERIFICATION_LOG.md §2.5）、本体も main_024 で直した。
いま残る役目は、本体が退行したときの見張り（VERIFICATION.md §3.1。合格条件は
`mixer = n/8 channel(s) busy` が1本を超えて増えていかないこと）なので、
曲を鳴らす口と止める口の記録と `mixer` の行は残した。
戦闘の開始・終了の9か所と `BattleEndManager.__init__` の包みは、経路の特定が済んでいるので外した。
版2までは、注入のたびのスナップショット（1回約 1.5KB。1プロセス平均6回ほど走る）と、
標本ごとの呼び出し元（8段。1段ごとに `__main__` の全クラスを総当たりする `owner_of` が走る）で、
`battle_bgm.log` の半分近くを占めていた。
スナップショットは `sys` の印で1プロセス1回にしてメインスレッドで取り、
呼び出し元は1プロセスあたり最初の数件だけにした。
標本の数えも1プロセスあたりにした。
包みは `safe=True` にし、受け取った引数をそのまま `orig` へ渡す。

版4: `mixer` の行が数えていたのは鳴っているチャンネルの全部で、効果音も入っていた。
戦闘の斬撃や画面の操作の音が鳴った瞬間に取ると、曲が1本でも `2/8` と出る。
チャンネルごとに鳴っている音（`Channel(i).get_sound()`）を読み、何の音かを書き添える。
見分けは GAME.md §2.11 に従う。曲は再生のたびに `Sound(パス)` で作られて `app.music` に入り、
効果音は起動時に読む `SoundManager.sounds`（15種）か、`play_sound_from_src` がその場で作る音。

    app.music     … 走っている app の `app.music` と同じ音（いま鳴らしている曲）
    target.music  … `play_music_from_src` / `stop_music` に渡された相手の `.music`（app でないとき）
    sounds[名前]  … `SoundManager.sounds` の音
    other         … どれでもない音。長さ（`get_length()`）を添える

曲の本数（`music=N`）は、`app.music` と `target.music` と、長さが `SONG_MIN_SECONDS` 以上の `other` の和。
長い `other` は誰の控えにも無い曲で、版2の頃の「止められなくなった曲」がこれに当たる。
行の頭（`n/8 channel(s) busy: [...]`）は版3と同じ形のまま、後ろに足した。
読むのは `get_busy` / `get_sound` / `get_length` だけで、音には触らない。
"""

import os
import sys
import threading

from instantale_modloader import frames, ui
from instantale_modloader.frames import repr_value

LOG_BASENAME = "battle_bgm.log"

# 起動直後に一度だけ、音まわりの今の姿を写し取る（版3で1プロセス1回）。
SNAPSHOT_ON_BOOT = True

# 呼び出し元として記録するフレーム数（自分のラッパを除いた直近ぶん）。
STACK_DEPTH = 8

# 曲を鳴らす／止める口の記録の上限（1プロセスあたり。版3）。
# 1プロセスで鳴らすのは数回〜十数回なので、見張りの行はほぼ全部残る。
MAX_SAMPLES = 200

# 呼び出し元を付ける件数（口ごと・1プロセスあたり。版3）。
# `frames.caller` は1段ごとに `owner_of` の総当たりが走るので、最初の数件で足りる。
MAX_CALLER_SAMPLES = 10

# 戦闘まわりの状態フラグ。
# 曲が切り替わった瞬間にどれが立っていたかを見る。
BATTLE_FLAGS = ("in_battle", "in_boss_battle", "in_colosseum_battle",
                "in_conversation", "in_free_input", "in_action_in_conversation")

# 誰の控えにも無い音を曲と数える長さ（秒。版4）。
# 画面の操作の効果音（`ui/...wav`）は数秒で、土地の曲はループの1周が数十秒から数分ある。
# 境目の音が出たときに読み直せるよう、`other` には長さを必ず添える。
SONG_MIN_SECONDS = 30.0

# スナップショットの印と標本の数え。
# 注入し直すと MOD のモジュールごと読み直されるので、モジュール変数では消える。`sys` に置く。
SNAPSHOT_MARK = "_instantale_probe_battle_bgm_snapshot"
COUNTS_ATTR = "_instantale_probe_battle_bgm_counts"


def _counts() -> dict:
    counts = getattr(sys, COUNTS_ATTR, None)
    if not isinstance(counts, dict):
        counts = {}
        setattr(sys, COUNTS_ATTR, counts)
    return counts


def apply(ctx):
    log_path = ctx.out_path(LOG_BASENAME)
    counts = _counts()

    write = ctx.logger(LOG_BASENAME)

    def sample(key, limit=MAX_SAMPLES):
        """枠が残っていれば1つ使って True。重い組み立てより先に呼ぶ。"""
        n = counts.get(key, 0)
        if n >= limit:
            return False
        counts[key] = n + 1
        return True

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

    def channels(target=None):
        """今いくつのチャンネルが鳴っているか ＝ 曲が重なっていないかの真実。

        `app.music` は最後に鳴らした曲しか指さないので、
        迷子になった曲はそこからは見えない。
        pygame に直接聞けば全部数えられる。

        版4: 鳴っているチャンネルごとに何の音かを添え、曲の本数（`music=N`）を別に出す
        （docstring の版4）。`target` は曲を鳴らす・止める口に渡された相手。
        """
        try:
            import pygame
            total = pygame.mixer.get_num_channels()
            busy = [i for i in range(total) if pygame.mixer.Channel(i).get_busy()]
        except Exception as exc:
            return "<mixer unavailable: {}>".format(type(exc).__name__)
        head = "{}/{} channel(s) busy: {}".format(len(busy), total, busy)
        try:
            known = known_sounds(target)
            kinds, music = [], 0
            for index in busy:
                kind, is_music = channel_kind(pygame.mixer.Channel(index).get_sound(), known)
                kinds.append("{}={}".format(index, kind))
                music += 1 if is_music else 0
            return "{}  music={} ({})".format(head, music, ", ".join(kinds) or "-")
        except Exception as exc:
            return "{}  music=? <{}>".format(head, type(exc).__name__)

    def known_sounds(target=None):
        """名前の分かる音を `{id(音): 名前}` で集める（版4）。読むだけ。"""
        known = {}
        app = find_app()
        manager = getattr(app, "sound_manager", None) if app is not None else None
        preloaded = getattr(manager, "sounds", None)
        if isinstance(preloaded, dict):
            for name, sound in preloaded.items():
                if sound is not None:
                    known[id(sound)] = "sounds[{}]".format(name)
        # 曲の控えを後に入れる（同じ音なら曲の名前を勝たせる）。
        if target is not None and target is not app:
            music = getattr(target, "music", None)
            if music is not None:
                known[id(music)] = "target.music"
        music = getattr(app, "music", None) if app is not None else None
        if music is not None:
            known[id(music)] = "app.music"
        return known

    def channel_kind(sound, known):
        """チャンネルで鳴っている音の名前と、曲として数えるか（版4）。"""
        if sound is None:
            return "<no sound>", False
        name = known.get(id(sound))
        if name is not None:
            return name, name.endswith(".music")
        try:
            length = float(sound.get_length())
        except Exception:
            return "other ?s", False
        return "other {:.1f}s".format(length), length >= SONG_MIN_SECONDS

    def sound_state(sound):
        try:
            return "{}#{:x} playing_on={}".format(
                type(sound).__name__, id(sound), sound.get_num_channels())
        except Exception:
            return repr(sound)

    def callers(key):
        """自分のラッパより手前のフレームを並べる。最初の数件だけ（版3）。

        段数で数えないこと（TECH.md §6.3）。
        `frames.caller` はローダと MOD のフレームを置き場所で飛ばすので、
        何段挟まっても正しい呼び出し元から並ぶ。
        上限を越えたら `frames.caller` を呼ばない（1段ごとに `owner_of` の総当たりが走る）。
        """
        if not sample("caller:" + key, MAX_CALLER_SAMPLES):
            return "<omitted>"
        return frames.caller(depth=STACK_DEPTH)

    def note(fn):
        """記録の失敗で本体の呼び出しを妨げない。"""
        try:
            fn()
        except Exception:
            ctx.log_exc("battle bgm probe: record failed")

    # ------------------------------------------------------- 起動時の一発計測
    def snapshot():
        """起動時の写し。1プロセス1回・メインスレッド（版3）。

        印はゲームの中（app と player が揃っている）で取れたときにだけ付ける。
        """
        if getattr(sys, SNAPSHOT_MARK, False):
            return
        app = find_app()
        if app is None or getattr(app, "player", None) is None:
            return
        setattr(sys, SNAPSHOT_MARK, True)

        write("=" * 78)
        write("battle bgm snapshot (pid {})".format(os.getpid()))
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

    def guarded_snapshot():
        try:
            snapshot()
        except Exception:
            ctx.log_exc("battle bgm probe: snapshot failed")

    if SNAPSHOT_ON_BOOT:
        ui.scheduler(ctx, "battle bgm probe snapshot")(guarded_snapshot)

    # ==================================================================
    # 曲を鳴らす/止める口。
    # ここが全ての判定材料で、VERIFICATION.md §3.1 の見張りもここの `mixer` の行を読む。
    # ==================================================================
    @ctx.wrap("scripts.sounds:SoundManager.play_music_from_src", required=False, safe=True)
    def play_music_from_src(orig, self, *args, **kwargs):
        # 版3: 上限を先に見る。越えたら何も組み立てずに素通しにする。
        if not sample("play_music_from_src"):
            return orig(self, *args, **kwargs)
        app = frames.arg(args, kwargs, "app", 0)

        def before():
            write("-" * 78)
            write("play_music_from_src({!r})".format(
                short_src(frames.arg(args, kwargs, "music_src", 1))))
            write("    target = {}".format(who(app)))
            write("    .music before = {!r}".format(
                short_src(getattr(app, "music", "<missing>"))))
            write("    flags  = {}".format(flags_of(app)))
            write("    caller = {}".format(callers("play_music_from_src")))
            write("    thread = {}".format(threading.current_thread().name))
        note(before)
        result = orig(self, *args, **kwargs)

        def after():
            write("    .music after  = {}".format(sound_state(getattr(app, "music", None))))
            write("    mixer  = {}".format(channels(app)))
        note(after)
        return result

    @ctx.wrap("scripts.sounds:SoundManager.stop_music", required=False, safe=True)
    def stop_music(orig, self, *args, **kwargs):
        if not sample("stop_music"):
            return orig(self, *args, **kwargs)
        app = frames.arg(args, kwargs, "app", 0)

        def before():
            write("stop_music() target={} .music={}".format(
                who(app), sound_state(getattr(app, "music", None))))
            write("    flags  = {}".format(flags_of(app)))
            write("    caller = {}".format(callers("stop_music")))
        note(before)
        result = orig(self, *args, **kwargs)
        note(lambda: write("    mixer after stop = {}".format(channels(app))))
        return result

    # 版3: 戦闘の開始と終了の経路（`start_battle_with_in_conversation` /
    # `execute_battle_process` / `BattleStartManager.start_battle` /
    # `BattlePhaseManager.check_battle_end` / `BattleEndManager.end_phase` / `.execute` /
    # `BattleEndInFreeAction.end_phase` / `BattleEndInColosseum.end_phase` /
    # `LootPhaseManager.looting_phase` と `BattleEndManager.__init__`）の包みは外した。
    # 原因は `BattleEndInFreeAction` が曲の持ち主に自分自身を渡すことで確定している
    # （VERIFICATION_LOG.md §2.5）。フラグの下ろし忘れの見張りは `107_` の `[FLAGFIX]` が出す。

    ctx.log("battle bgm probe log: {}".format(log_path))
