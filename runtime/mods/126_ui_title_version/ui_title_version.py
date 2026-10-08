# -*- coding: utf-8 -*-
"""追加: タイトル画面の隅に「MOD が入っている」ことを出す。

ローダは注入で入るので、**ゲームの見た目はどこにも変わらない**。
起動しただけでは MOD が効いているのか注入し損ねたのかが分からず、
不具合の報告も「MOD 入りかどうか」の確認から始まる（`out/status.json` は遊んでいる人が開く場所ではない）。
タイトル画面の隅に版を1行出しておけば、それが答えになる。

出すのはタイトル画面だけ。
ラベルは `StartScreen` の子なので、
「開始する」を押して画面が入れ替われば一緒に消える。
遊んでいる間の画面には何も足さない。

## 置き方はローダの `ui` に任せる

タイトル画面の見つけ方（注入した時点で出ている画面と、後で組まれる画面の両方）は
`ui.on_title_screen`、隅への置き方（同じ隅の他の MOD の1行と縦に積む、フォントを画面から写す、
置き直しで重ねない）は `ui.corner_label` が持つ。
`136_` もタイトル画面の右上に1行出すので、同じ部品で積んで重ならないようにした。
この MOD の1行はいちばん縁に近い所（`TITLE_ORDER`）。

## 画面の子を1枚増やしてよいのか

HUD では駄目だった。
「画面の最初の子」を取る側が居て、そこへ直接足すとアイテムの移動・装備が効かなくなる（`ui.overlay_host` の註、
VERIFICATION_LOG.md §2.33）。
タイトル画面は `scripts.hud.hud_start` の中で閉じており、
子を数える側も引く側も居ない。
それでも足すのはいちばん手前の隅の箱 1枚だけで、ゲームが組んだ子には触らない。

## 読み込みは `001_` の次

番号は 1xx だが、`load_order.json` では `001_crash_recorder` の直後に置いてある。
この MOD は誰も包まず、誰にも包まれない（`StartScreen.__init__` は `136_` も包むが、
どちらも組み終わった後に飾るだけで順序に依らない）ので順序の制約を持たない。
帯順が効くのは同じ対象を 2つの MOD が包むときだけ（TECH.md §3.2.2）。
並びの意味としては、
リコンとクラッシュ記録に続く「ゲームではなくローダ自身のことを扱う3本目」。
"""

from instantale_modloader import ui

# 出す文字。
# `{version}` はローダの版、`{mods}` は当たった MOD の本数。
TEXT = "modloader v{version}"

# 出す隅（`ui.CORNERS` の名前）。
# 右下はゲーム自身のバグレポートのボタンの場所。
CORNER = "右上"

# 文字の大きさ（px）。
# `ui.upx` でゲーム側の拡縮に合わせる。
FONT_SIZE = 14

# 文字の濃さ。
# タイトルの絵より目立たないところに置いてある。
ALPHA = 0.55

#: 自分が足したラベルの印。
#: `ui.MOD_WIDGET_PREFIX` で始めること（ui.py の註）。
LABEL_ATTR = ui.MOD_WIDGET_PREFIX + "title_version"

#: 同じ隅に積む中での並び（小さいほど縁に近い）。`136_` は 10。
TITLE_ORDER = 0


def _mod_count():
    """この注入で当たった MOD の本数。数えられなければ None。

    `status()` は全 MOD の適用が済んでから固まる。
    `on_ready` はその後に流れ、タイトルへ戻ったときの組み直しはさらに後なので、
    どちらから来ても数は揃っている。
    """
    try:
        import instantale_modloader as loader

        results = loader.status().get("mods") or {}
    except Exception:
        return None
    return sum(1 for value in results.values() if value == "ok")


def apply(ctx):

    def text():
        count = _mod_count()
        raw = TEXT if isinstance(TEXT, str) else ""
        return (raw.replace("{version}", str(ctx.version))
                   .replace("{mods}", "?" if count is None else str(count)))

    def decorate(screen):
        """タイトル画面にラベルを1枚置く。置き直しであって、重ね置きではない。"""
        return ui.corner_label(screen, LABEL_ATTR, text(), corner=CORNER, order=TITLE_ORDER,
                               font_size=FONT_SIZE, alpha=ALPHA) is not None

    ui.on_title_screen(ctx, decorate, key="126_ui_title_version")
    ctx.log("title version: installed ({!r} at {})".format(TEXT, CORNER))
