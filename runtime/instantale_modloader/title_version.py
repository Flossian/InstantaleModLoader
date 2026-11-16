# -*- coding: utf-8 -*-
"""タイトル画面の右上に `modloader vX.Y.Z` の1行を出す（TECH.md §5.14）。

ローダは注入で入るので、ゲームの見た目はどこも変わらない。
この1行が出ていれば注入が成功している（README §1.4 の確かめ方）。
出すのはタイトル画面だけで、ラベルは `StartScreen` の子なので「開始する」で画面が入れ替われば一緒に消える。

v1.21.0 までは `126_ui_title_version` という MOD だった。
MOD のままだと切った時に README の確かめ方が成り立たないので、ローダに取り込んで設定と入切を外した。
旧フォルダと設定値は `drop_renamed` が片付ける（`ABSORBED_MODS`）。

置き方は `ui.on_title_screen` / `ui.corner_label` が持つ。
`136_` のモデル名の行も同じ隅に積み、この行がいちばん縁に近い（`TITLE_ORDER`）。
"""

from . import ui

#: 自分が足したラベルの印。MOD だった頃と同じ名前にして、注入し直したときに前の世代の1行を置き換える。
LABEL_ATTR = ui.MOD_WIDGET_PREFIX + "title_version"

#: 同じ隅に積む中での並び（小さいほど縁に近い）。`136_` は 10。
TITLE_ORDER = 0

#: `on_title_screen` の鍵とログの頭。
KEY = "title version"

FONT_SIZE = 14
ALPHA = 0.55


def text(version) -> str:
    return "modloader v{}".format(version)


def install(ctx) -> bool:
    """タイトル画面の飾りを積む。`boot()` が `(loader)` の持ち主で呼ぶ。戻りは `on_ready` に積めたか。"""
    shown = text(ctx.version)

    def decorate(screen):
        return ui.corner_label(screen, LABEL_ATTR, shown, corner="右上", order=TITLE_ORDER,
                               font_size=FONT_SIZE, alpha=ALPHA) is not None

    return ui.on_title_screen(ctx, decorate, key=KEY)
