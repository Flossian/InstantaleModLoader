# -*- coding: utf-8 -*-
"""ゲームのデータを読み書きする部品。この MOD の方針は持たない。

ここに置いてあるのは「Instantale が手配度と所持金をどこに持っているか」だけで、
いくら取るか・いつ差し出すか・何と表示するかは入口の `office_pardon.py` にある。
設定（`mod.json` から変えられる値）も入口の定数なので、ここでは読まない。
必要な値は引数で受け取る。

根拠は実セーブ（`savedata.json` の復号。GAME.md §2.16 / §2.20）:

    player_data.area_history = {
        "0": {"residency": {...}, "achievements": [...], "lawfulness": 10},
        "1": {...}, ...                 ← エリア id ごとに1件
    }
    player_data.gold          = 270975
    player_data.current_area  = "0"

`area_history` は実行時もこの形（セーブはそのまま json 化したもの）だが、
辞書とは限らないという前提で書いてある。
読めなければ `None` を返し、呼ぶ側がそこで諦める。

手配度の**読み方**はローダ（`instantale_modloader.ui`）に移した。
同じ読み方を要る MOD が3本になったため（TECH.md §3.2.3）。
ここに残っているのは書き戻す側と所持金。
"""

from instantale_modloader import ui

#: 手配度の実体。
#: 負の値ほど重い（0 未満で犯罪者、既定の平常値は 10）。
#: **読み書きはローダの語彙**（`ui.lawfulness_by_area` ほか。TECH.md §3.2.3）。
LAWFULNESS_KEY = ui.LAWFULNESS_KEY

#: 名前をここに残しているのは呼び出し側の見た目のため。中身はローダと同じ。
area_history_of = ui.area_history_of
history_entry = ui.area_record
lawfulness_of = ui.lawfulness_of
set_lawfulness = ui.set_lawfulness


def gold_of(player):
    """所持金。読めなければ `None`。"""
    value = getattr(player, "gold", None)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(value)


def set_gold(player, value):
    """所持金を書き戻す。書けたら `True`。"""
    try:
        player.gold = int(value)
    except Exception:
        return False
    return True


def current_facility(app):
    """いま居る施設。引き当てられなければ `None`。

    `player.location` は施設のオブジェクトとは限らない。
    セーブでは `"0"` という
    id の文字列だった（`ui.current_area` が同じ理由で両対応になっている）。
    """
    player = getattr(app, "player", None)
    if player is None:
        return None
    location = getattr(player, "location", None)
    if isinstance(location, (str, int)):
        facility, _node = ui.find_facility(ui.current_area(app), str(location))
        return facility
    return location


def refresh_status(app):
    """所持金の表示を今の値に合わせる。効かなくても処理は続ける。

    `InstantaleApp.update_ui(self, *args)` はゲーム自身が画面上部の情報を塗り直すのに使っている入口。
    無ければ何もしない（次にゲームが塗ったときに合う）。
    """
    updater = getattr(app, "update_ui", None)
    if not callable(updater):
        return False
    try:
        updater()
        return True
    except Exception:
        return False
