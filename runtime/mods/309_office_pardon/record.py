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
所持金の読み書きと表示の塗り直しもローダ（`ui.gold_of` / `set_gold(refresh=True)`）。
ここに残っているのは手配度を書き戻す側。
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


