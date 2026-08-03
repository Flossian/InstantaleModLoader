# -*- coding: utf-8 -*-
"""修正: 戦闘が終わっても `in_battle` が 1 のまま残るのを直す。

## 原因（GAME.md §2.10 / VERIFICATION.md §2.6）

戦闘終了マネージャは2つあり、**フラグを下ろすのは片方だけ**。

| 終了マネージャ | `end_phase` 完了時の `in_battle` | 入口 |
|---|---|---|
| `BattleEndManager` | **0**（ゲーム自身が下ろしている） | クエスト中のエンカウントなど通常の戦闘 |
| `BattleEndInFreeAction` | **1**（下ろし忘れ） | 自由入力・**会話から入った戦闘** |

`106_`（戦闘BGM）の「復帰呼び出しが app ではなく `self` を渡している」と
**同じマネージャの、同じ種類の書き忘れ**。したがってこの修正は値を発明していない。
**ゲーム自身が通常経路でやっていることを、抜けている経路にも適用するだけ**
（`101_` が `clamp_npc_difficulty_value` を当てたのと同じ形）。

## 残ると何が起きるか

`in_battle` は**セーブに入り、ロード時の分岐に使われている**。戦闘後に保存すると
**次にロードしたとき戦闘BGMで始まる**。さらに mod 側でもこのフラグを「戦闘中は
出さない」条件に使っているので、一度戦闘するとその後ずっとイベントが出なくなる。

## 触る範囲

**下ろすのは `in_battle` だけ。** 1→0 の遷移が確認できているのがこれだけで、
`in_boss_battle` / `in_colosseum_battle` は一度も立っているところを観測していない
（立っていたら記録だけ残す）。ゲームが既に下ろしている場合は何もしない。
`BattleEndManager` の経路ではこの mod は無音で素通りする。

タイミングもゲームに合わせる。`BattleEndManager` は `end_phase` を抜けた時点で
0 になっているので、こちらも `end_phase` の復帰直後に下ろす。

ロード時にも見る。**既に `in_battle=True` で保存されてしまったセーブ**があるため
（この mod を入れる前に保存したもの）、読み込み直後に立っていたら下ろす。ロード直後に
戦闘が復元されていることは無い。戦闘の中身（敵・戦闘ログ）は復元されず、
残っていたフラグだけが効いている状態なので、これは残骸の後始末にあたる。
"""

import datetime
import sys

from instantale_modloader import frames

LOG_BASENAME = "battle_bgm.log"   # 106_ / 207_ と同じ時系列で読めるようにする

# 下ろすフラグ。実測で「ゲーム自身が下ろしている」ことを確認できたものだけ。
CLEAR_FLAGS = ("in_battle",)

# 立っていたら記録だけする（下ろさない）。観測できていないため。
REPORT_FLAGS = ("in_boss_battle", "in_colosseum_battle")

# ロード直後にも残骸を下ろすか。
CLEAR_ON_LOAD = True

# 注入した時点で既に立っている残骸も下ろすか。
# **戦闘中に注入した場合を巻き込まないよう、敵が居ないことを確認してから下ろす**
# （`app.current_enemy_dict` が空。実測: 残骸のとき `len=0`）。
CLEAR_ON_BOOT = True


def apply(ctx):
    log_path = ctx.out_path(LOG_BASENAME)

    def write(text):
        try:
            with open(log_path, "a", encoding="utf-8") as fh:
                fh.write("[{}] [FLAGFIX] {}\n".format(
                    datetime.datetime.now().isoformat(timespec="milliseconds"), text))
        except Exception:
            ctx.log_exc("battle flag: write failed")

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

    def clear_stale(app, where):
        """立ったままのフラグを下ろす。ゲームが既に下ろしていれば何もしない。"""
        if app is None:
            return
        stale = [name for name in CLEAR_FLAGS if getattr(app, name, False)]
        for name in stale:
            try:
                setattr(app, name, False)
            except Exception:
                ctx.log_exc("battle flag: could not clear {}".format(name))
                return
        others = [name for name in REPORT_FLAGS if getattr(app, name, False)]
        if stale:
            write("{}: cleared {} (the game left it set)".format(
                where, ", ".join(stale)))
        if others:
            # 観測できていない組み合わせ。出たら設計を見直す材料になる。
            write("{}: {} still set -- not touching (never observed being cleared)"
                  .format(where, ", ".join(others)))

    # ------------------------------------------------------- 戦闘終了マネージャ
    # 3種類とも同じ扱いにしてよい。ゲームが下ろしている経路では stale が空になり、
    # この mod は何も書かずに素通りする（＝正しい経路には触れない）。
    def on_end(target, label):
        @ctx.wrap(target, required=False)
        def _end(orig, self, *args, **kwargs):
            result = orig(self, *args, **kwargs)
            try:
                clear_stale(getattr(self, "app", None) or find_app(), label)
            except Exception:
                ctx.log_exc("battle flag: clear failed")
            return result
        return _end

    on_end("__main__:BattleEndManager.end_phase", "BattleEndManager.end_phase")
    on_end("__main__:BattleEndInFreeAction.end_phase", "BattleEndInFreeAction.end_phase")
    on_end("__main__:BattleEndInColosseum.end_phase", "BattleEndInColosseum.end_phase")

    # ------------------------------------------------------------ ロード直後
    # この mod を入れる前に保存したセーブには、立ったままのフラグが焼かれている。
    def on_load(target, label):
        @ctx.wrap(target, required=False)
        def _load(orig, self, *args, **kwargs):
            result = orig(self, *args, **kwargs)
            try:
                # `hasattr` は使わない ― 失敗するルックアップを1回起こすので
                # `__getattr__` トリップワイヤ（`201_`）を自己発火させる。
                # 既定値付きの読み取りなら「本当に読まれた1回」と区別が付かない
                # （TECH.md §6.3。この mod は初版が `hasattr` のままだった）。
                owner = self if frames.attr(self, "in_battle") is not frames.MISSING \
                    else find_app()
                clear_stale(owner, label)
            except Exception:
                ctx.log_exc("battle flag: clear failed")
            return result
        return _load

    if CLEAR_ON_LOAD:
        on_load("__main__:InstantaleApp.load_game_new", "load_game_new")
        on_load("__main__:InstantaleApp.start_game", "start_game")

    # ------------------------------------------------------------- 注入した時点
    # 既にこの mod 無しで戦闘を終えているセッションには、残骸が立ったままになって
    # いる。**戦闘中に注入した場合を巻き込まないよう、敵が居ないことを確かめる。**
    if CLEAR_ON_BOOT:
        try:
            app = find_app()
            enemies = getattr(app, "current_enemy_dict", None) if app else None
            if app is not None and isinstance(enemies, dict) and not enemies:
                clear_stale(app, "injection")
            elif app is not None and getattr(app, "in_battle", False):
                write("injection: in_battle is set and enemies are present "
                      "-- looks like a real battle; not touching")
        except Exception:
            ctx.log_exc("battle flag: boot clear failed")

    ctx.log("battle flag fix: clearing {} at battle end{} log={}".format(
        "/".join(CLEAR_FLAGS), " and on load" if CLEAR_ON_LOAD else "", log_path))
