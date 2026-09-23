# -*- coding: utf-8 -*-
"""修正: 戦闘が終わっても `in_battle` が 1 のまま残るのを直す。

## 原因（GAME.md §2.10 / VERIFICATION_LOG.md §2.6）

戦闘終了マネージャは3つあり、**フラグを下ろし切るのは一部だけ**。

| 終了マネージャ | `end_phase` 完了時 | 入口 |
|---|---|---|
| `BattleEndManager` | `in_battle` は 0（ゲーム自身が下ろす）。**`in_colosseum_battle` は 1 のまま** | 通常の戦闘と、**闘技場からの撤退** |
| `BattleEndInFreeAction` | `in_battle` が 1（下ろし忘れ） | 自由入力・会話から入った戦闘 |
| `BattleEndInColosseum` | 両方 0（ゲーム自身が下ろす） | 闘技場で勝ったとき |

`106_`（戦闘BGM）の「復帰呼び出しが app ではなく
`self` を渡している」と **同じマネージャの、同じ種類の書き忘れ**。
したがってこの修正は値を発明していない。
**ゲーム自身が通常経路でやっていることを、
抜けている経路にも適用するだけ**（`101_` が
`clamp_npc_difficulty_value` を当てたのと同じ形）。

## 残ると何が起きるか

`in_battle` は**セーブに入り、ロード時の分岐に使われている**。
戦闘後に保存すると **次にロードしたとき戦闘BGMで始まる**。
さらに mod 側でもこのフラグを「戦闘中は出さない」条件に使っているので、
一度戦闘するとその後ずっとイベントが出なくなる。

## 闘技場から撤退すると次の戦闘が落ちる

`in_colosseum_battle` が残ったまま次の戦闘に入ると、
**その戦闘の終わりにゲームが `BattleEndInColosseum` を選ぶ**。
闘技場に立っていないので施設の `config` に `current_phase` が無く、
`KeyError: 'current_phase'`（`instantale.py:8070`）でワーカースレッドが死ぬ。
画面は待機表示のまま止まる。

実測の並び（`233_probe_colosseum` のログ）。

    闘技場の試合 → 撤退 → BattleEndManager(end_type='escaped') → 旗は 1 のまま
    依頼の戦闘の開始 → battle_start の flags に in_colosseum_battle=1
    依頼の戦闘の終わり → BattleEndInColosseum.end_phase → KeyError

## 触る範囲

下ろすのは `in_battle` と `in_colosseum_battle` の2つ。
どちらも**ゲーム自身が下ろしている経路がある**（前者は `BattleEndManager`、
後者は `BattleEndInColosseum`）ので、抜けている経路にも同じことをするだけで、
値を発明してはいない。
`in_boss_battle` は立っているところを一度しか観測しておらず、
戦闘の後に 0 へ戻るのも見えている（GAME.md §2.10）ので、
こちらは立っていたら記録だけ残す。
ゲームが既に下ろしている場合は何もしない
（勝った試合の `BattleEndInColosseum` の経路では無音で素通りする）。

タイミングもゲームに合わせる。
`BattleEndManager` は `end_phase` を抜けた時点で 0 になっているので、
こちらも `end_phase` の復帰直後に下ろす。

ロード時にも見る。
**既に `in_battle=True` で保存されてしまったセーブ**があるため（この
mod を入れる前に保存したもの）、読み込み直後に立っていたら下ろす。

戦闘の最中に保存したセーブは、`game_variables` に `in_battle`（`"normal"` のような戦闘の種類）・
`in_colosseum_battle`・`current_enemy_data`（敵）・`buttons`（`攻撃` など）が入っている。
ロードの後の画面は2通りあった（実機）。

| ロードの後 | 旗を下ろすと | 旗を残すと |
|---|---|---|
| 戦闘のボタンが戻る | 戦闘の欄が畳まれたまま（大きさ 0）で、攻撃した後の敵の欄の描画が `ZeroDivisionError`（`new_hud.py:2306`）で落ちた | 戦闘の続き |
| 場所のボタン（移動など）が出る | 普段どおり | 旗と敵が残ったまま依頼を始め、闘技場の相手と依頼の敵が1つの戦闘に混ざった |

敵の有無では見分けられない（どちらも `current_enemy_dict` にセーブの敵が戻る）。
なので**ロードの後に並んだボタンが戦闘のものなら触らない**。
そうでなければ旗を下ろし、残っている敵も空にする（戦闘を普通に終えたときのゲームと同じ状態）。
戦闘の最中のセーブの中身とロード後の2通りは GAME.md §2.10。
"""

import sys

from instantale_modloader import frames, ui

LOG_BASENAME = "battle_bgm.log"   # 106_ / 207_ と同じ時系列で読めるようにする

# 下ろすフラグ。
# 実測で「ゲーム自身が下ろしている経路がある」ことを確認できたものだけ。
#   in_battle           … `BattleEndManager` の経路で 0 になる
#   in_colosseum_battle … 勝った試合（`BattleEndInColosseum`）で 0 になる。
#                         撤退（`BattleEndManager`）では残り、次の戦闘の終わりに落ちる
CLEAR_FLAGS = ("in_battle", "in_colosseum_battle")

# 立っていたら記録だけする（下ろさない）。
# ボス戦の旗は戦闘の後に自分で 0 へ戻るところまで観測できている。
REPORT_FLAGS = ("in_boss_battle",)

# ロード直後に「戦闘の画面に戻った」と見なすボタンのクラス（`攻撃` / `スキル・防御` /
# `発言する` と、スキルを選んでいる最中の `やめる`）。
BATTLE_BUTTON_CLASSES = ("BattlePhaseManager", "SkillChoicePhaseManager",
                         "UtteranceChoiceInBattleManager", "UtteranceInBattleManager",
                         "CancelBattleActionManager")

# ロード直後にも残骸を下ろすか。
CLEAR_ON_LOAD = True

# 注入した時点で既に立っている残骸も下ろすか。
# **戦闘中に注入した場合を巻き込まないよう、
# 敵が居ないことを確認してから下ろす**（`app.current_enemy_dict` が空。
# 実測: 残骸のとき `len=0`）。
CLEAR_ON_BOOT = True


def apply(ctx):
    log_path = ctx.out_path(LOG_BASENAME)
    write = ctx.logger(LOG_BASENAME, tag="[FLAGFIX]")

    find_app = ui.find_app     # 走っている app の探し方はローダの語彙

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
            # 観測できていない組み合わせ。
            # 出たら設計を見直す材料になる。
            write("{}: {} still set -- not touching (never observed being cleared)"
                  .format(where, ", ".join(others)))

    def in_real_battle(app):
        """敵が居るか（本物の戦闘の最中か）。残骸のときは `current_enemy_dict` が空（実測）。"""
        enemies = getattr(app, "current_enemy_dict", None) if app is not None else None
        return isinstance(enemies, dict) and bool(enemies)

    def battle_on_screen(app):
        """いま並んでいるボタンが戦闘のものか。ロード直後の見分けに使う。

        ロードでは敵を見ても分からない。ゲームは戦闘の最中に保存したセーブの敵を、
        戦闘の画面に戻らなかったときも `current_enemy_dict` に持ち続ける（実機）。
        """
        buttons = getattr(app, "buttons", None) if app is not None else None
        if not isinstance(buttons, list):
            return False
        return any(ui.spec_cls_name(entry) in BATTLE_BUTTON_CLASSES for entry in buttons)

    def clear_stale_enemies(app, where):
        """戦闘の画面でないのに残っている敵を空にする（戦闘を終えたときのゲームと同じ状態）。"""
        enemies = getattr(app, "current_enemy_dict", None)
        if isinstance(enemies, dict) and enemies:
            names = list(enemies)
            try:
                enemies.clear()
            except Exception:
                ctx.log_exc("battle flag: could not clear the stale enemies")
                return
            write("{}: cleared {} stale enem{} ({})".format(
                where, len(names), "y" if len(names) == 1 else "ies", ", ".join(names)))

    # ------------------------------------------------------- 戦闘終了マネージャ
    # 3種類とも同じ扱いにしてよい。
    # ゲームが下ろしている経路では stale が空になり、
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
                # `hasattr` は使わない。
                # 失敗するルックアップを1回起こすので
                # `__getattr__` トリップワイヤ（`201_`）を自己発火させる。
                # 既定値付きの読み取りなら「本当に読まれた1回」と区別が付かない（TECH.md
                # §6.3）。
                owner = self if frames.attr(self, "in_battle") is not frames.MISSING \
                    else find_app()
                if battle_on_screen(owner):
                    write("{}: the battle screen came back -- the save was made "
                          "mid-battle; not touching".format(label))
                elif any(getattr(owner, name, False) for name in CLEAR_FLAGS):
                    seen = [ui.spec_cls_name(entry)
                            for entry in (getattr(owner, "buttons", None) or [])]
                    write("{}: not a battle screen (buttons: {})".format(
                        label, ", ".join(str(name) for name in seen) or "none"))
                    clear_stale(owner, label)
                    clear_stale_enemies(owner, label)
            except Exception:
                ctx.log_exc("battle flag: clear failed")
            return result
        return _load

    if CLEAR_ON_LOAD:
        on_load("__main__:InstantaleApp.load_game_new", "load_game_new")
        on_load("__main__:InstantaleApp.start_game", "start_game")

    # ------------------------------------------------------------- 注入した時点
    # 既にこの mod 無しで戦闘を終えているセッションには、
    # 残骸が立ったままになっている。
    # 戦闘中に注入した場合を巻き込まないよう、敵が居ないことを確かめる。
    if CLEAR_ON_BOOT:
        try:
            app = find_app()
            enemies = getattr(app, "current_enemy_dict", None) if app else None
            if app is not None and isinstance(enemies, dict) and not in_real_battle(app):
                clear_stale(app, "injection")
            elif app is not None and getattr(app, "in_battle", False):
                write("injection: in_battle is set and enemies are present "
                      "-- looks like a real battle; not touching")
        except Exception:
            ctx.log_exc("battle flag: boot clear failed")

    ctx.log("battle flag fix: clearing {} at battle end{} log={}".format(
        "/".join(CLEAR_FLAGS), " and on load" if CLEAR_ON_LOAD else "", log_path))
