# -*- coding: utf-8 -*-
"""追加: タイトル画面の隅に「MOD が入っている」ことを出す。

ローダは注入で入るので、**ゲームの見た目はどこにも変わらない**。起動しただけでは
MOD が効いているのか注入し損ねたのかが分からず、不具合の報告も「MOD 入りかどうか」
の確認から始まる（`out/status.json` は遊んでいる人が開く場所ではない）。
タイトル画面の隅に版を1行出しておけば、それが答えになる。

出すのは**タイトル画面だけ**。ラベルは `StartScreen` の子なので、「開始する」を
押して画面が入れ替われば一緒に消える。遊んでいる間の画面には何も足さない。

## 2つの経路から足す

注入はウィンドウが出てから行われる（TECH.md §1.4）ので、**注入した時点の
タイトル画面はもう組み上がっている**。`__init__` を包むだけでは、目の前の画面には
何も出ず、一度タイトルへ戻るまで確かめられない。

| いつのタイトル画面 | 足し方 |
|---|---|
| 注入した時点で出ているもの | `ctx.on_ready` で窓の中を辿って見つけ、その場で足す |
| その後に組まれるもの（タイトルへ戻る） | `StartScreen.__init__` を包み、組み終わりに足す |

どちらも同じ `decorate()` を呼ぶ。`apply()` は1プロセスで何度も呼ばれるので
（TECH.md §3.4）、**足す前に自分の印が付いたラベルを外す**。`on_ready` のキーに
世代を混ぜてあるのは、注入し直したときに目の前のラベルも新しい版へ置き換わる
ようにするため（TECH.md §3.6.1）。

## 画面の子を1枚増やしてよいのか

HUD では駄目だった。「画面の最初の子」を取る側が居て、そこへ直接足すと
アイテムの移動・装備が効かなくなる（`ui.overlay_host` の註、
VERIFICATION_LOG.md §2.33）。タイトル画面は `scripts.hud.hud_start` の中で
閉じており、子を数える側も引く側も居ない。それでも足すのは**いちばん手前に
1枚だけ**で、ゲームが組んだ子には触らない。

## 読み込みは `001_` の次

番号は 1xx だが、`load_order.json` では `001_crash_recorder` の直後に置いてある。
この MOD は誰も包まず、誰にも包まれない（対象は `StartScreen.__init__` 1つで、
他の MOD は触っていない）ので**順序の制約を持たない**。帯順が効くのは同じ対象を
2つの MOD が包むときだけ（TECH.md §3.2.2）。並びの意味としては、リコンと
クラッシュ記録に続く「ゲームではなくローダ自身のことを扱う3本目」。

## フォントは画面から写す

既定の文字は ASCII だが、設定で日本語にできる。Kivy の既定フォント（Roboto）に
日本語は無いので、**タイトル画面のウィジェットからフォント名を写す**
（写さないと豆腐になる。`116_` と同じ理由）。写す先が見つからなければ既定のまま
にする。ASCII の既定文字なら問題なく出る。
"""

import os

from instantale_modloader import frames, ui

# 出す文字。`{version}` はローダの版、`{mods}` は当たった MOD の本数。
TEXT = "modloader v{version}"

# 出す隅（`ui.CORNERS` の名前）。右下はゲーム自身のバグレポートのボタンの場所。
CORNER = "右上"

# 文字の大きさ（px）。`ui.upx` でゲーム側の拡縮に合わせる。
FONT_SIZE = 14

# 文字の濃さ。タイトルの絵より目立たないところに置いてある。
ALPHA = 0.55

#: 包む対象。タイトル画面はここで組み上がる。
TARGET = "scripts.hud.hud_start:StartScreen.__init__"

#: タイトル画面が居るモジュールと、その中の画面クラス。
SCREEN_MODULE = "scripts.hud.hud_start"
SCREEN_CLASS = "StartScreen"

#: 自分が足したラベルの印。`ui.MOD_WIDGET_PREFIX` で始めること（ui.py の註）。
LABEL_ATTR = ui.MOD_WIDGET_PREFIX + "title_version"

#: ウィジェットを辿る深さの上限。タイトル画面は数階層しか無いが、
#: 親子が輪になっているビルドでも止まるように上限を持たせる。
MAX_DEPTH = 12

#: Kivy の既定フォント。**日本語を持たない**ので、これを写しても意味が無い
#: （写さなかったのと同じ）。見つけても採らず、他のウィジェットを見に行く。
DEFAULT_FONT = "Roboto"


def _walk(roots, want, depth=MAX_DEPTH):
    """ウィジェットの木を上から辿り、`want(ウィジェット)` が真のものを集める。"""
    found = []
    stack = [(widget, 0) for widget in roots]
    seen = set()
    while stack:
        widget, level = stack.pop()
        if id(widget) in seen:
            continue
        seen.add(id(widget))
        if want(widget):
            found.append(widget)
        if level >= depth:
            continue
        children = frames.attr(widget, "children", None)
        if isinstance(children, (list, tuple)):
            stack.extend((child, level + 1) for child in children)
    return found


def _screen_class():
    """`StartScreen` を型として引く。まだ import されていなければ None。"""
    import sys

    module = sys.modules.get(SCREEN_MODULE)
    cls = frames.attr(module, SCREEN_CLASS, None) if module is not None else None
    return cls if isinstance(cls, type) else None


def _open_title_screens():
    """今出ているタイトル画面。**属性名ではなく型**で見分ける。"""
    cls = _screen_class()
    if cls is None:
        return []
    try:
        from kivy.core.window import Window

        roots = list(Window.children)
    except Exception:
        return []          # Kivy が引けない環境（オフライン検証）
    return _walk(roots, lambda widget: isinstance(widget, cls))


def _font_of(screen):
    """タイトル画面のウィジェットからフォント名を写す。取れなければ None。"""
    for widget in _walk([screen], lambda _widget: True, depth=4):
        name = frames.text_of(widget, "font_name")
        if name and os.path.basename(name).split(".")[0] != DEFAULT_FONT:
            return name
    return None


def _mod_count():
    """この注入で当たった MOD の本数。数えられなければ None。

    `status()` は全 MOD の適用が済んでから固まる。`on_ready` はその後に流れ、
    タイトルへ戻ったときの組み直しはさらに後なので、どちらから来ても数は揃っている。
    """
    try:
        import instantale_modloader as loader

        results = loader.status().get("mods") or {}
    except Exception:
        return None
    return sum(1 for value in results.values() if value == "ok")


def _fit_to_text(label, size):
    """文字の大きさをそのままラベルの大きさにする（隅からはみ出させない）。"""
    try:
        label.size = size
    except Exception:
        pass


def _strip(screen):
    """自分が前に足したラベルを外す。何枚あっても全部。"""
    children = frames.attr(screen, "children", None)
    if not isinstance(children, (list, tuple)):
        return
    for child in list(children):
        if frames.attr(child, LABEL_ATTR, None) is None:
            continue
        try:
            screen.remove_widget(child)
        except Exception:
            pass           # 外せなくても、この後 1枚に上書きされるわけではない


def apply(ctx):

    def text():
        count = _mod_count()
        raw = TEXT if isinstance(TEXT, str) else ""
        return (raw.replace("{version}", str(ctx.version))
                   .replace("{mods}", "?" if count is None else str(count)))

    def decorate(screen):
        """タイトル画面にラベルを1枚置く。**置き直しであって、重ね置きではない。**"""
        try:
            from kivy.uix.label import Label
        except Exception:
            return False   # Kivy が引けない環境。出せないだけで害は無い
        _strip(screen)
        label = Label(text=text(), size_hint=(None, None),
                      pos_hint=dict(ui.CORNERS.get(CORNER, ui.CORNERS["右上"])),
                      color=(1.0, 1.0, 1.0, float(ALPHA)))
        font = _font_of(screen)
        if font:
            label.font_name = font
        label.font_size = ui.upx(FONT_SIZE)
        setattr(label, LABEL_ATTR, True)
        # 文字の箱に合わせておかないと、ラベルの既定の大きさ（100x100）のまま
        # 隅に寄るので、文字が窓からはみ出す。
        label.bind(texture_size=_fit_to_text)
        label.texture_update()
        _fit_to_text(label, frames.attr(label, "texture_size", None) or label.size)
        screen.add_widget(label)
        return True

    def decorate_open():
        """注入した時点で出ているタイトル画面（もう組み上がっている）。"""
        screens = _open_title_screens()
        for screen in screens:
            decorate(screen)
        ctx.log("title version: {} title screen(s) already open".format(len(screens)))

    @ctx.wrap(TARGET, safe=True)
    def start_screen_init(orig, self, *args, **kwargs):
        result = orig(self, *args, **kwargs)
        decorate(self)      # ここで壊れても safe=True が画面を守る
        return result

    # キーに世代を混ぜる。混ぜないと、注入し直したときに目の前のラベルが
    # 古い版のまま残る（TECH.md §3.6.1）。
    ctx.on_ready(decorate_open,
                 key="126_ui_title_version:decorate:{}".format(ctx.generation))
    ctx.log("title version: installed ({!r} at {})".format(TEXT, CORNER))
