# -*- coding: utf-8 -*-
"""修正: 戦闘が終わったのに戦闘 BGM が鳴り続ける／曲が重なるのを直す。

## 実測で判明した原因（2026-07-26、`207_` の計測。実プレイ12戦）

BGM は pygame の `Sound` オブジェクトで、`play_music_from_src(app, src)` が
**`app.music` に差し替えて再生**し、`stop_music(app)` が **`app.music` を止める**。
つまり `app.music` が「今鳴っている曲」の唯一の取っ手で、ここを失った曲は
**誰にも止められなくなる**。

戦闘終了時、ゲームは確かに元の曲へ戻す呼び出しをしている。ところが:

```
戦闘開始 instantale.py:6957  play_music_from_src(battle) app.music before=<Sound 5B0> after=<Sound 850>
戦闘終了 instantale.py:7993  play_music_from_src(area)   app.music before=<missing>       ← ★
        instantale.py:7339  stop_music()                app.music =<Sound 850>（戦闘曲）
次の戦闘 instantale.py:6963  stop_music()                app.music =<Sound 850>（まだ戦闘曲）
```

★の行だけ `app.music` が **`<missing>`（属性が無い）**。同じ瞬間に読んだ
フラグも、他の行が `in_battle=1 in_free_input=1` なのにここだけ全部 0 だった。
**戦闘終了の復帰呼び出しは、app ではない別のオブジェクトを渡している**
（毎回 `<missing>` に戻るので、使い捨てのインスタンス ＝ マネージャ自身の `self`
とみられる）。`None` ではなく属性そのものが無い点が決定的で、タイトルに戻った
直後の app は `music=None` と出る。

このため:

1. 復帰呼び出しは `app.music` を止められない ― **戦闘曲が鳴ったまま**その上に
   エリア曲が重なる
2. 鳴り始めたエリア曲は使い捨てオブジェクトにぶら下がる ― **迷子**になり、
   以後どの `stop_music` でも止まらない
3. 次の戦闘では `app.music`（＝戦闘曲）だけが止められ、迷子のエリア曲は鳴り続ける
   ― 戦闘中もエリア曲が重なる

## 直し方

**pygame に直接聞く。** `Sound.get_num_channels()` は「その曲が今いくつの
チャンネルで鳴っているか」を返すので、自前の帳簿ではなく**実際に鳴っているか**で
判定できる。`play_music_from_src` を包んで、鳴らされた `Sound` を全部控えておき、
戦闘が終わったところで:

    戦闘中でないのに戦闘曲が鳴っていれば止める
    迷子のエリア曲は **止めずに `app.music` へ入れ直す**（＝引き取らせる）。
      止めて鳴らし直すと曲が頭からになるし、引き取れば以後ゲーム自身の
      `stop_music` が効くようになるので、次の戦闘からは普通に切り替わる
    それでも重なっているぶんと、正体不明の音は止める
    何も鳴らなくなったら、戦闘前に鳴っていた曲を app 経由で鳴らし直す

**チャンネルを直接見るので、注入より前に迷子になった曲も拾える。** 実際、最初の
計測では町に立っているだけで **8/8 チャンネルが埋まり、そのうち app が握って
いた曲は1つも鳴っていなかった**（12戦ぶんの迷子が全部鳴っていた）。

判定は戦闘終了マネージャ（3種）と、選択肢が普通の状態に戻ったところの2つから
起こす。どちらも `RESTORE_DELAY` 秒の猶予を置くので、ゲーム側の処理と競争しない。
**pygame を触るのは Kivy の Clock（メインスレッド）から**（`300_` で確立した作法。
戦闘処理は別スレッドで走る）。
"""

import datetime
import sys
import time

LOG_BASENAME = "battle_bgm.log"      # 207_ の計測と同じログに時系列で並べる

# 戦闘曲かどうかの判定。Assets/sounds/musics/battle/ 配下だけが戦闘曲。
BATTLE_DIR_MARK = "/musics/battle/"

# 戦闘終了を検知してから実際に見に行くまでの猶予（秒）。
RESTORE_DELAY = 2.5

# 判定を予約する間隔の下限（秒）。ボタンの張り替えは頻繁に起きる。
ARM_INTERVAL = 2.0

# 控えておく Sound の数。迷子は最大でも戦闘の回数ぶんしか増えない。
MAX_TRACKED = 12

# この3つが1つでも立っていれば戦闘中とみなし、何もしない。
BATTLE_FLAGS = ("in_battle", "in_boss_battle", "in_colosseum_battle")

# 注入した時点で既に迷子になっている曲を掃除するか。
# 迷子はプロセスが終わるまで鳴り続けるので、既定で有効にしてある。
SWEEP_ON_BOOT = True


def apply(ctx):
    log_path = ctx.out_path(LOG_BASENAME)

    state = {
        "manager": None,     # SoundManager のインスタンス
        "tracks": [],        # 鳴らされた曲。古い順に [{src, sound, owned}]
        "restore": None,     # 戦闘曲でない最後の src ＝ 戦闘前に鳴っていた曲
        "sfx": [],           # 効果音の Sound の id（掃除の除外用）
        "armed_at": 0.0,
        "battle_started_at": 0.0,
        # 「本当に戦闘が走っているか」。フラグ（`in_battle`）は戦闘が終わっても
        # 1 のまま残るので使えない（GAME.md §2.4 / `107_`）。開始と終了のフックで持つ。
        "battle_active": False,
    }

    def write(text):
        try:
            with open(log_path, "a", encoding="utf-8") as fh:
                fh.write("[{}] [BGMFIX] {}\n".format(
                    datetime.datetime.now().isoformat(timespec="milliseconds"), text))
        except Exception:
            ctx.log_exc("bgm restore: write failed")

    def short(value):
        if not isinstance(value, str) or not value:
            return repr(value)
        parts = value.replace("\\", "/").rstrip("/").split("/")
        return "/".join(parts[-2:]) if len(parts) >= 2 else value

    def is_battle_track(src):
        if not isinstance(src, str) or not src:
            return False
        return BATTLE_DIR_MARK in ("/" + src.replace("\\", "/").lstrip("/")).lower()

    def audible(sound):
        """その Sound が今いくつのチャンネルで鳴っているか。ここが唯一の真実。"""
        try:
            return sound is not None and sound.get_num_channels() > 0
        except Exception:
            return False

    def find_app():
        main = sys.modules.get("__main__")
        cls = getattr(main, "InstantaleApp", None)
        try:
            from kivy.app import App
            app = App.get_running_app()
            if app is not None and (cls is None or isinstance(app, cls)):
                return app
        except Exception:
            pass
        if main is not None and isinstance(cls, type):
            for value in vars(main).values():
                if isinstance(value, cls):
                    return value
        return None

    def in_battle(app):
        return any(getattr(app, flag, False) for flag in BATTLE_FLAGS)

    def battle_running(app):
        """本当に戦闘中か。**フラグだけを信じてはいけない。**

        `in_battle` は戦闘が終わっても 1 のまま残る（実測: 連戦したとき、次の
        戦闘の開始処理 `instantale.py:6963` の時点でも 1 だった。0 に戻るのは
        タイトルから入り直したときだけ）。フラグを鵜呑みにすると、戦闘後の
        後始末が永久に走らない ― 実際に最初の版はこれで空振りした。
        `in_shopping` が常時 True だった件（`300_`）と同じ性質の罠。

        そこで**フラグが立っていて、かつ実際に戦闘曲らしき音が鳴っている**
        ときだけ戦闘中とみなす。正体不明の音を含めるのは、注入より前に
        始まった戦闘（＝こちらが src を知らない戦闘曲）を巻き込まないため。
        """
        if not in_battle(app):
            return False
        if any(is_battle_track(e["src"]) and audible(e["sound"])
               for e in state["tracks"]):
            return True
        manager = state.get("manager") or getattr(app, "sound_manager", None)
        known = set(id(e["sound"]) for e in state["tracks"])
        return any(id(sound) not in known
                   for _i, _ch, sound in stray_channels(app, manager))

    def stale_battle_flag(app):
        """フラグは戦闘中なのに戦闘の実体が無い ＝ 残骸。

        判定は `current_enemy_dict` が空かどうか（実測: 残骸のとき `len=0`）。
        `combat_log` は前の戦闘の本文が残ったままなので使えない。
        """
        if not in_battle(app):
            return False
        enemies = getattr(app, "current_enemy_dict", None)
        return isinstance(enemies, dict) and not enemies

    def area_bgm(app):
        area = getattr(getattr(app, "player", None), "current_area", None)
        bgm = getattr(area, "bgm", None)
        return bgm if isinstance(bgm, str) and bgm else None

    # ------------------------------------------------------------- 曲の追跡
    # 値は一切変えない。鳴らされた Sound を控えるだけ。
    @ctx.wrap("scripts.sounds:SoundManager.play_music_from_src")
    def play_music_from_src(orig, self, app, music_src, *args, **kwargs):
        result = orig(self, app, music_src, *args, **kwargs)
        try:
            state["manager"] = self
            # play_music_from_src は渡されたオブジェクトの .music に Sound を入れる。
            # つまりここで取れるのが「今鳴り始めた曲」そのもの。
            sound = getattr(app, "music", None)
            owned = app is find_app()
            state["tracks"].append({"src": music_src, "sound": sound, "owned": owned})
            del state["tracks"][:-MAX_TRACKED]
            if is_battle_track(music_src):
                # 予約した後で新しい戦闘が始まったかどうかの判定に使う。
                state["battle_started_at"] = time.monotonic()
                if not state["battle_active"]:
                    # 戦闘が走っていないのに戦闘曲が鳴った。実測ではロード直後に
                    # 起きる ― ゲームのロード処理が `in_battle` を見て曲を選んで
                    # おり（`instantale.py:1458`＝戦闘 / `:1460`＝エリア）、下ろし
                    # 忘れたフラグがセーブに焼かれていると戦闘曲の枝を引く
                    # （`107_` がフラグ側を直すが、既に保存されたセーブには
                    #  焼き込まれているので、鳴ってしまった側もここで直す）。
                    write("battle track outside a battle: {} -- "
                          "no battle has started in this process".format(
                              short(music_src)))
                    arm("battle track outside a battle", force=True,
                        trust_end=True, prefer_area=True)
            else:
                state["restore"] = music_src
            if not owned:
                # ★ 原因そのもの。ゲームはここで app ではないものを渡している。
                write("orphan: {} was attached to {} instead of the app "
                      "-- nothing can stop it now".format(
                          short(music_src), type(app).__name__))
        except Exception:
            ctx.log_exc("bgm restore: tracking failed")
        return result

    @ctx.wrap("scripts.sounds:SoundManager.stop_music")
    def stop_music(orig, self, app, *args, **kwargs):
        try:
            state["manager"] = self
        except Exception:
            pass
        return orig(self, app, *args, **kwargs)

    # 効果音も控えておく。掃除は「app.music でも効果音でもない音」を狙うので、
    # その場で作られた効果音（`SoundManager.sounds` に無いもの）を巻き込まない
    # ようにするための除外リスト。
    def track_sfx(target):
        @ctx.wrap(target, required=False)
        def _sfx(orig, self, app, *args, **kwargs):
            result = orig(self, app, *args, **kwargs)
            try:
                state["manager"] = self
                sound = getattr(app, "sound", None)
                if sound is not None:
                    state["sfx"].append(id(sound))
                    del state["sfx"][:-MAX_TRACKED]
            except Exception:
                pass
            return result
        return _sfx

    track_sfx("scripts.sounds:SoundManager.play_sound")
    track_sfx("scripts.sounds:SoundManager.play_sound_from_src")

    # ----------------------------------------------------------- 後始末の判定
    def needs_sweep(app):
        """今すぐ見に行く価値があるか。予約を無駄に積まないための足切り。"""
        if any(is_battle_track(e["src"]) and audible(e["sound"])
               for e in state["tracks"]):
            return True
        manager = state.get("manager") or getattr(app, "sound_manager", None)
        return bool(stray_channels(app, manager))

    def sfx_sounds(manager, app):
        """効果音の Sound 一式。掃除で巻き込まないための「触ってはいけない」集合。

        `SoundManager.sounds` は起動時に読み込んだ効果音15種の辞書
        （'斬撃' / 'ダメージ1' / '接敵（通常）' ...）。曲は再生のたびに
        `Sound(パス)` で作られるので、**この辞書に居ないループ音は曲**と言える。
        """
        keep = set()
        try:
            for sound in getattr(manager, "sounds", {}).values():
                keep.add(id(sound))
        except Exception:
            pass
        keep.add(id(getattr(app, "sound", None)))
        keep.update(state["sfx"])
        return keep

    def stray_channels(app, manager):
        """`app.music` でも効果音でもないのに鳴っている音 ＝ 迷子の曲。

        自前の帳簿（`tracks`）は注入以降のぶんしか無いが、チャンネルを直接見れば
        **注入より前に迷子になった曲も拾える**。実際、注入時点で 8/8 チャンネルが
        埋まっていた（2026-07-26 の実測）。
        """
        found = []
        try:
            import pygame
            keep = sfx_sounds(manager, app)
            app_music = getattr(app, "music", None)
            for index in range(pygame.mixer.get_num_channels()):
                channel = pygame.mixer.Channel(index)
                if not channel.get_busy():
                    continue
                sound = channel.get_sound()
                if sound is None or sound is app_music or id(sound) in keep:
                    continue
                found.append((index, channel, sound))
        except Exception:
            ctx.log_exc("bgm restore: channel scan failed")
        return found

    def sweep(reason, trust_end=False, armed_at=0.0, prefer_area=False):
        """後始末。`trust_end` は「戦闘終了マネージャを抜けた直後」という意味。

        フラグが当てにならない（`battle_running` 参照）ので、**戦闘が終わった
        という事実はフックそのものから取る**。予約してから実際に見に行くまでの
        間に次の戦闘が始まっていないかだけ、別途確かめる。
        """
        app = find_app()
        if app is None:
            return
        if trust_end:
            if state.get("battle_started_at", 0.0) > armed_at:
                return                  # 予約後に次の戦闘が始まった。触らない
        elif battle_running(app):
            return
        manager = state.get("manager") or getattr(app, "sound_manager", None)
        if getattr(manager, "is_fading", False):
            # フェード中に切ると不自然に途切れる。少し待ってからやり直す。
            arm(reason + " (retry after fade)", force=True, trust_end=trust_end,
                prefer_area=prefer_area)
            return

        # 注入以降に鳴らした曲は Sound から src を引ける。迷子が「戦闘曲」なのか
        # 「戻すはずだったエリア曲」なのかは、これで見分ける。
        src_of = dict((id(e["sound"]), e["src"]) for e in state["tracks"]
                      if e["sound"] is not None)
        done = []

        def stop_channel(channel, label, index):
            try:
                channel.stop()
                done.append("stopped {} on channel {}".format(label, index))
            except Exception:
                ctx.log_exc("bgm restore: channel stop failed")

        # 1. app.music 自身が戦闘曲のまま鳴っていれば止める。
        #    （持ち主は正しいので、ゲーム側の stop_music が来れば止まる曲。
        #      来ないまま戦闘が終わっているのがこの症状）
        app_music = getattr(app, "music", None)
        if audible(app_music) and is_battle_track(src_of.get(id(app_music))):
            try:
                app_music.stop()
                done.append("stopped the battle track held by the app")
            except Exception:
                ctx.log_exc("bgm restore: stop failed")

        # 2. app.music でも効果音でもないのに鳴っている音＝迷子。
        #    戦闘曲・正体不明は止める。エリア曲だと分かっているものは残して
        #    引き取り候補にする（止めて鳴らし直すと曲が頭からになるため）。
        adoptable = []
        for index, channel, sound in stray_channels(app, manager):
            src = src_of.get(id(sound))
            if src is not None and not is_battle_track(src):
                adoptable.append((index, channel, sound, src))
            else:
                stop_channel(channel, short(src) if src else "stray track", index)

        # チャンネル番号は鳴り始めた順ではないので、控えの並び順で新しい方を選ぶ。
        played_at = dict((id(e["sound"]), i) for i, e in enumerate(state["tracks"])
                         if e["sound"] is not None)
        adoptable.sort(key=lambda item: played_at.get(id(item[2]), -1))

        # 3. 迷子のエリア曲は、いちばん新しい1本だけ残して app に引き取らせる。
        #    `app.music` に入れ直すだけで、以後はゲーム自身の stop_music が効く
        #    ようになり、次の戦闘でも普通に切り替わる。
        for index, channel, _sound, src in adoptable[:-1]:
            stop_channel(channel, "duplicate {}".format(short(src)), index)
        if adoptable:
            index, channel, sound, src = adoptable[-1]
            if audible(getattr(app, "music", None)):
                stop_channel(channel, "duplicate {}".format(short(src)), index)
            else:
                try:
                    app.music = sound
                    state["restore"] = src
                    done.append("handed {} back to the app (channel {})".format(
                        short(src), index))
                except Exception:
                    ctx.log_exc("bgm restore: adoption failed")

        # 4. 何も鳴らなくなったら、戦闘前の曲を **app 経由で** 鳴らし直す。
        if done and not audible(getattr(app, "music", None)):
            # 普段は「戦闘直前に鳴っていた曲」が正解。ただしロード直後は控えが
            # 前のセッションのものなので、いま居るエリアの曲を優先する。
            target = ((area_bgm(app) or state.get("restore")) if prefer_area
                      else (state.get("restore") or area_bgm(app)))
            if target and manager is not None:
                try:
                    manager.play_music_from_src(app, target)
                    done.append("restarted {} on the app".format(short(target)))
                except Exception:
                    ctx.log_exc("bgm restore: restart failed")
            else:
                done.append("nothing to restart to")

        if done:
            write("sweep after {}: {}".format(reason, "; ".join(done)))

    def arm(reason, force=False, trust_end=False, prefer_area=False):
        now = time.monotonic()
        if not force and now - state.get("armed_at", 0.0) < ARM_INTERVAL:
            return
        state["armed_at"] = now
        try:
            from kivy.clock import Clock
        except Exception:
            ctx.log_exc("bgm restore: kivy Clock unavailable")
            return
        # pygame を触るのはメインスレッドに寄せる。戦闘処理は別スレッドで走る。
        Clock.schedule_once(
            lambda _dt: _guarded(reason, trust_end, now, prefer_area), RESTORE_DELAY)

    def _guarded(reason, trust_end, armed_at, prefer_area):
        try:
            sweep(reason, trust_end, armed_at, prefer_area)
        except Exception:
            ctx.log_exc("bgm restore: sweep failed")

    # ------------------------------------------------- 1. 戦闘終了マネージャ
    # 実測では会話・自由入力からの戦闘は BattleEndInFreeAction を通る。
    # 他の2つも同じ後始末で構わないので、まとめて仕掛ける。
    # 戦闘が始まった／終わったという事実を、フックそのもので持つ。
    # 曲が鳴るのは `start_battle` を抜けた約1秒後（Clock 経由）なので、
    # 開始は**入る前**に立てておかないと「戦闘外の戦闘曲」と誤判定する。
    @ctx.wrap("__main__:BattleStartManager.start_battle", required=False)
    def start_battle(orig, self, *args, **kwargs):
        try:
            state["battle_active"] = True
        except Exception:
            pass
        return orig(self, *args, **kwargs)

    def on_end(target, label):
        @ctx.wrap(target, required=False)
        def _end(orig, self, *args, **kwargs):
            result = orig(self, *args, **kwargs)
            try:
                state["battle_active"] = False
                # ここを抜けた ＝ 戦闘は終わった。フラグより確かな合図なので
                # trust_end で渡し、`in_battle` の居残りに邪魔させない。
                arm(label, force=True, trust_end=True)
            except Exception:
                ctx.log_exc("bgm restore: arm failed")
            return result
        return _end

    on_end("__main__:BattleEndManager.end_phase", "BattleEndManager.end_phase")
    on_end("__main__:BattleEndInFreeAction.end_phase", "BattleEndInFreeAction.end_phase")
    on_end("__main__:BattleEndInColosseum.end_phase", "BattleEndInColosseum.end_phase")

    # ------------------------------------------------- 2. 世界に戻ったところ
    # 上のどれも通らない経路が万一あっても、選択肢が普通の状態に戻れば必ず通る。
    # 迷子の曲は戦闘の外でも重なりうるので、この保険には独立した意味がある。
    def on_back_to_world(target, label):
        @ctx.wrap(target, required=False)
        def _back(orig, self, *args, **kwargs):
            result = orig(self, *args, **kwargs)
            try:
                if not battle_running(self) and needs_sweep(self):
                    arm(label)
            except Exception:
                ctx.log_exc("bgm restore: arm failed")
            return result
        return _back

    on_back_to_world("__main__:InstantaleApp.set_buttons_to_normal",
                     "set_buttons_to_normal")
    on_back_to_world("__main__:InstantaleApp.refresh_choice_buttons",
                     "refresh_choice_buttons")

    # ------------------------------------------- 注入前に鳴り始めた戦闘曲の置換
    def heal_after_load(reason):
        """「フラグは戦闘中・敵は居ない」状態で鳴っている曲を、エリア曲に置き換える。

        注入より前に鳴り始めた曲は src の控えが無く、正体が分からない。だが
        ロード処理は `in_battle` を見て曲を選ぶ（`instantale.py:1458`＝戦闘 /
        `:1460`＝エリア）ので、**残骸フラグのまま読み込まれたセーブでは戦闘曲が
        鳴っている**。エリア曲で確実に置き換える。元から正しい曲だった場合は
        同じ曲が鳴り直るだけで実害が無い。
        """
        app = find_app()
        enemies = getattr(app, "current_enemy_dict", None) if app is not None else None
        if app is None or not (isinstance(enemies, dict) and not enemies):
            return                      # この間に戦闘が始まっていたら触らない
        manager = state.get("manager") or getattr(app, "sound_manager", None)
        target = area_bgm(app)
        if manager is None or not target:
            return
        music = getattr(app, "music", None)
        if audible(music):
            try:
                music.stop()
            except Exception:
                ctx.log_exc("bgm restore: stop failed")
        try:
            manager.play_music_from_src(app, target)
        except Exception:
            ctx.log_exc("bgm restore: replace failed")
            return
        write("{}: in_battle was set with no enemies -- replaced the playing "
              "track with {}".format(reason, short(target)))

    def _guarded_heal(reason):
        try:
            heal_after_load(reason)
        except Exception:
            ctx.log_exc("bgm restore: heal failed")

    # --------------------------------------------------------- 注入時の後始末
    # 迷子の曲はプロセスが終わるまで鳴り続ける（タイトルに戻っても残っていた）。
    # 注入した時点で既に溜まっているぶんも掃除する ― 実際、最初の計測では
    # 町に立っているだけで 8/8 チャンネルが埋まっていた。
    if SWEEP_ON_BOOT:
        try:
            arm("injection", force=True)
            # 残骸フラグの判定は**今この場で**行う。`107_` が後から適用されて
            # フラグを下ろすので、2.5秒後に見に行くと判定できなくなる。
            boot_app = find_app()
            if boot_app is not None and stale_battle_flag(boot_app):
                from kivy.clock import Clock
                Clock.schedule_once(
                    lambda _dt: _guarded_heal("injection"), RESTORE_DELAY)
        except Exception:
            ctx.log_exc("bgm restore: boot sweep failed")

    ctx.log("battle bgm fix: delay={:.1f}s log={}".format(RESTORE_DELAY, log_path))
