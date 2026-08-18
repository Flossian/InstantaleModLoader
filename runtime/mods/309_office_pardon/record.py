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
"""

from instantale_modloader import ui

#: 手配度の実体。
#: 負の値ほど重い（0 未満で犯罪者、既定の平常値は 10）。
LAWFULNESS_KEY = "lawfulness"


def area_history_of(player):
    """エリアごとの記録 `{area_id: 記録}`。読めなければ `None`。"""
    value = getattr(player, "area_history", None)
    return value if isinstance(value, dict) else None


def history_entry(player, area_id):
    """そのエリアの記録。無ければ `None`（＝一度も訪れていない）。

    id は保存されるときに文字列になるが、
    実行中に int で入っていることもありうるので、
    素の引きが外れたら文字列に均して引き直す。
    """
    history = area_history_of(player)
    if history is None or area_id in (None, ""):
        return None
    if area_id in history:
        return history[area_id]
    wanted = str(area_id)
    for key, value in history.items():
        if str(key) == wanted:
            return value
    return None


def lawfulness_of(entry):
    """記録の手配度。読めなければ `None`。

    `bool` を弾いているのは `isinstance(True, int)` が真だから。
    True を手配度 1 と読むと、そこから金額まで計算してしまう。
    """
    if entry is None:
        return None
    value = entry.get(LAWFULNESS_KEY) if isinstance(entry, dict) \
        else getattr(entry, LAWFULNESS_KEY, None)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(value)


def set_lawfulness(entry, value):
    """手配度を書き戻す。書けたら `True`。

    項目を新設しないのは呼ぶ側の責任（先に
    `lawfulness_of` で読めた記録にしか渡さない）。
    ここで無条件に作ると、手配度を持たないビルドにそれらしい項目が生えて、
    以後どちらが本物か分からなくなる。
    """
    if entry is None:
        return False
    try:
        if isinstance(entry, dict):
            entry[LAWFULNESS_KEY] = int(value)
        else:
            setattr(entry, LAWFULNESS_KEY, int(value))
    except Exception:
        return False
    return True


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
