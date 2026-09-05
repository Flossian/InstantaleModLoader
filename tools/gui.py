# !/usr/bin/env python3 -*- coding: utf-8 -*-
"""Instantale ModLoader の GUI。

やることは6つ。

  1. `runtime/mods/` の mod を **適用順に**一覧で見せる
  2. 順序と有効/無効を編集して `load_order.json` に書き戻す
  3. **mod ごとの設定**を編集して `mod_settings.json` に書き戻す
  4. ゲームを起動して、準備が整った時点で注入する
  5. 注入の**結果**を出す（何本入ったか、何が失敗したか、どこが重なったか）
  6. mod を追加する / 外す

一覧の中身は `mod.json` から読む。
**mod のコードは一切 import しない**（一覧を作るためだけに他人の
mod のトップレベルを走らせない）。
並び順の意味は上が先で、上から順に適用される。

探索と適用順の判定は**ローダ本体の `discover()` を呼ぶ**。
以前はここに同じ規則（`_` で始まるフォルダを除く / `mod.json` を持つものだけ
/ 未記載は末尾）を書き写していたので、片方だけ直すと一覧と実際の適用順がずれた。

名前は日本語と英語を別の列に出す。
`mod.json` は片方しか持たないことがあるので、
無い側はもう片方で埋める（列を空のまま並べるより、同じ文字を2つ並べる方が読める）。

有効/無効は `load_order.json` の `"disabled"` に入る。
フォルダ名を変えて切る方式（先頭に `_`）を GUI から使うと、
`"order"` の中の名前と食い違うため。

注入と起動は watcher.py の処理をそのまま呼ぶ。
GUI 側で条件判定を書き直すと `watch.bat` と挙動がずれるため、
待ち方（インタプリタの初期化＋ウィンドウの出現）は1か所へ置いたままにする。

使い方:
    python gui.py       （通常は ..\\InstantaleModLoader.bat から開く）
"""

from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
import sys
import queue
import threading
import time
import tkinter as tk
import zipfile
from tkinter import filedialog, font as tkfont, messagebox, ttk

# このファイルは tools/ にある。
# runtime/ と設定は1階層上（配布フォルダの根）。
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "runtime"))

import injector                                  # noqa: E402
import logrotate                                 # noqa: E402
import watcher                                   # noqa: E402
import instantale_modloader as ml                # noqa: E402
from instantale_modloader import config as C     # noqa: E402

RUNTIME_DIR = os.path.join(ROOT, "runtime")
MODS_DIR = os.path.join(RUNTIME_DIR, "mods")
# 書き戻す先はローダに聞く。
# 手元用の `load_order.local.json` が在ればそちら（未公開の
# MOD を手元で動かしている間、配布用のファイルを書き換えないため）。
# ここで `ORDER_NAME` を直に組むと、GUI で保存するたびに手元の
# MOD が配布用の順序ファイルへ書き戻される。
OUT_DIR = os.path.join(ROOT, "out")
STATUS_PATH = os.path.join(OUT_DIR, ml.STATUS_NAME)

# MOD が持つ永続データ（進行中の道中、依頼の出所、NPC の控え）。
# out/ とは別。
# 場所はローダに聞く（`ml.state_dir`）― ゲームの中で書く側と
# GUI で開く側とで同じ場所を組み立てる規則を2箇所に書かないため。
STATE_DIR = ml.state_dir(RUNTIME_DIR)

# 選んだ値は全部 settings/ に集める（mod ごとの設定は
# instantale_modloader.config が同じフォルダへ mod_settings.json を書く）。
# ここに入るのは「このウィンドウの覚えていること」＝ゲームの場所と窓の大きさ。
SETTINGS_DIR = C.settings_dir(RUNTIME_DIR)
CONFIG_PATH = os.path.join(SETTINGS_DIR, "gui.json")

# 更新の確認先。起動のたびに別スレッドで1回だけ見る（`App._check_update`）。
RELEASE_API = ("https://api.github.com/repos/Flossian/InstantaleModLoader"
               "/releases/latest")
# 上書きで消えないもの。確認の文に出す（zip に入らないので展開は触らない）。
UPDATE_KEEPS = "settings\\・state\\・local\\・手元で足した MOD"


def _vtuple(ver: str) -> tuple[int, ...]:
    return tuple(int(x) for x in ver.lstrip("v").split("."))


def newer_release(current: str = ml.__version__) -> tuple[str, str] | None:
    """GitHub の最新 Release が今より新しければ (版, full zip の URL)。無ければ None。

    ネットが無い・API の形が違う・版が数字でない、はどれも例外のまま返す。
    呼ぶ側（別スレッド）が握って黙る。
    """
    import urllib.request
    with urllib.request.urlopen(RELEASE_API, timeout=5) as r:
        data = json.load(r)
    ver = str(data["tag_name"]).lstrip("v")
    if _vtuple(ver) <= _vtuple(current):
        return None
    for asset in data.get("assets", []):
        if asset["name"].endswith("-full.zip"):
            return ver, asset["browser_download_url"]
    return None


def extract_release(zip_path: str, dest: str = ROOT) -> int:
    """full zip を dest へ上書き展開して、書いたファイルの数を返す。

    zip の頭一段（`InstantaleModLoader-<ver>/`）は剥がす。
    zip に無いものは消さない ― 手元で作っている MOD を巻き込むので。
    新しい版で無くなった MOD は、配る側がデバッグモード限定か読み込まない形にして
    出す約束（消す判断をここでしない）。

    開発の作業ツリーで押すと、配布物の中身で作業中の変更が上書きされる
    （2026-09-05 に実際に起きた。git で戻せるが、戻す前に何を失ったか見ること）。
    """
    count = 0
    with zipfile.ZipFile(zip_path) as z:
        for info in z.infolist():
            parts = info.filename.split("/")[1:]
            if info.is_dir() or not parts or ".." in parts:
                continue
            path = os.path.join(dest, *parts)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with z.open(info) as src, open(path, "wb") as dst:
                shutil.copyfileobj(src, dst)
            count += 1
    return count


FIND_POLL = 1.0      # ゲームのプロセスを探す間隔（秒）
FIND_TRIES = 60      # 何回まで探すか（Epic 経由だと立ち上がりが遅いので長めに）

# 適用結果（status.json の ["mods"]）を日本語にする。
RESULT_TEXT = {
    "ok": "適用",
    "no-entry": "入口なし",
    "load-error": "読込失敗",
    "apply-error": "適用失敗",
    "no-apply": "apply なし",
    "api-too-new": "ローダが古い",
    "api-too-old": "mod が古い",
}


# --------------------------------------------------------------------------
# 配色とフォント
# --------------------------------------------------------------------------
# 色は全部ここに置く。
# 以前は widget を作る場所に "#666" のような文字列が直に書いてあって、
# 1色変えるのに散らばった箇所を追う必要があった。
# 名前は「何色か」ではなく「何に使うか」で付ける ― 後で暗い配色に振るときに、
# 使う側を書き換えずに済むように。
PALETTE = {
    "bg":         "#f4f5f7",   # 窓の地
    "surface":    "#ffffff",   # 一覧・入力欄。地より一段手前にあるもの
    "raised":     "#eceef2",   # 触れているボタン
    "pressed":    "#e0e3e9",   # 押されているボタン
    "border":     "#d4d7dd",   # 仕切り線・一覧の外枠
    # 押せるもの（ボタン・入力欄）の輪郭。
    # 地が #f4f5f7 で中が白だと、その差は 256 段階で 11 しかない ― 面の色では境目が出ないので、
    # 線で示すしかない。
    # 仕切り線と同じ濃さでは足りないため、一段濃い色を別に持つ。
    "control_edge": "#b0b8c4",
    "text":       "#1f2328",   # 本文
    "text_sub":   "#57606a",   # 補足（説明・状態・見出し）
    "text_faint": "#646b76",   # 既定値・無効な行・小見出し
    "accent":     "#2f6feb",   # 主操作（注入して起動）
    "accent_lit": "#4681f2",   # 主操作に触れている
    "accent_dim": "#1f57c3",   # 主操作を押している
    "accent_off": "#a8bfe8",   # 主操作が押せないとき
    "on_accent":  "#ffffff",   # 主操作の上に乗る字
    "on_accent_off": "#f2f6fd",
    "select":     "#dce7fb",   # 一覧の選択行
    "check":      "#2f6feb",   # 入っているチェックの地（主操作と同じ明るい青）
    "check_edge": "#9aa2ad",   # 入っていないチェックの枠
    "danger":     "#b3261e",   # 前回の注入で入らなかった mod
    "warn":       "#9a5b00",   # 警告の行
    # 普段の遊びの一覧に**並ばない**行の地。
    # 文字色は無効=灰・失敗=赤で使い切っているので、こちらは背景で分ける。
    # 伏せる理由ごとに色を変えてある ―
    # デバッグモードを入れると3種類が同時に並ぶので、
    # 1色だと「なぜ出ているのか」が混ざる。
    "dev_bg":     "#fdf3df",   # 計測。選択色（#dce7fb）と離すため暖色に振る
    "wip_bg":     "#e0f2dc",   # 開発中（9xx）。作りかけ＝これから育つ、で緑
    "taken_bg":   "#e7e9ec",   # 取込済。降ろしたものなので色味を抜く
    # `local/` から読んだ行の地（配る予定が無い MOD。TECH.md §2.6.2）。
    # 選択色も薄い青なので、そちらを青寄り・こちらをシアン寄りに離してある
    # （同じ青みだと、選んでいるのか `local/` なのかが読めない）。
    "local_bg":   "#dbf1f6",
}

# 日本語と英語が同じ列に並ぶので、両方が同じ太さで出る書体を選ぶ。
# 上から順に探して、入っていなければ Tk の既定のまま（環境によっては Yu Gothic UI が無い ― その場合に読めない字が出るより、
# 既定の書体で出る方がまだ良い）。
FONT_CANDIDATES = ("Yu Gothic UI", "Meiryo UI", "Segoe UI")
FONT_SIZE = 10


def setup_theme(root: tk.Tk) -> ttk.Style:
    """配色と書体を決める。窓を作った直後・中身を組む前に1回だけ呼ぶ。

    土台に **clam** を敷く。
    Windows の既定は `vista` で、これはボタンや入力欄を OS に描かせるため
    `style.configure` の色がほとんど効かない（配色を1か所にまとめても反映されないので、
    まとめる意味が無くなる）。
    clam は全部 Tk 側で描くので指定が通る。

    戻り値の `Style` は使わなくても良いが、
    後から名前付きの style を足したいときのために返しておく。
    """
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass            # clam が無い Tk。既定のテーマのまま先へ進む

    p = PALETTE

    # -- 書体。名前付きフォントを差し替えると、個別に font= を書いていない
    #    widget（一覧・ボタン・ラベル）が揃って追随する。
    families = set(tkfont.families(root))
    family = next((f for f in FONT_CANDIDATES if f in families), None)
    for name in ("TkDefaultFont", "TkTextFont", "TkHeadingFont", "TkMenuFont"):
        try:
            font = tkfont.nametofont(name, root)
        except tk.TclError:
            continue
        if family:
            font.configure(family=family)
        font.configure(size=FONT_SIZE)

    root.configure(background=p["bg"])
    # コンボボックスの一覧は ttk ではなく素の Listbox なので、
    # style ではなく option データベース越しにしか色を渡せない。
    root.option_add("*TCombobox*Listbox.background", p["surface"])
    root.option_add("*TCombobox*Listbox.foreground", p["text"])
    root.option_add("*TCombobox*Listbox.selectBackground", p["select"])
    root.option_add("*TCombobox*Listbox.selectForeground", p["text"])

    style.configure(".",
                    background=p["bg"], foreground=p["text"],
                    fieldbackground=p["surface"], bordercolor=p["border"],
                    lightcolor=p["border"], darkcolor=p["border"],
                    troughcolor=p["bg"], focuscolor=p["accent"],
                    selectbackground=p["select"], selectforeground=p["text"])
    style.configure("TFrame", background=p["bg"])
    style.configure("TLabel", background=p["bg"], foreground=p["text"])
    style.configure("TSeparator", background=p["border"])

    # -- ラベルの役割。
    # 呼ぶ側は色ではなく「何のラベルか」を指定する。
    style.configure("Title.TLabel",
                    font=(family or "", FONT_SIZE + 5, "bold"))
    style.configure("Sub.TLabel", foreground=p["text_sub"])
    style.configure("Faint.TLabel", foreground=p["text_faint"])
    style.configure("Warn.TLabel", foreground=p["warn"])
    # ボタンの群に付ける小さな見出し。
    # 区切り線だけだと「なぜここで切れているか」が伝わらないので、群の名前を出す。
    style.configure("Group.TLabel", foreground=p["text_faint"],
                    font=(family or "", FONT_SIZE - 1, "bold"))
    # 右のパネルの見出し（選んでいる mod の名前）。一覧の字より一段大きくして、
    # 視線を一覧に戻さなくても「いまどれを見ているか」が分かるようにする。
    style.configure("InfoName.TLabel", foreground=p["text"],
                    font=(family or "", FONT_SIZE + 2, "bold"))

    # -- ボタン。既定は地に沈む見た目にして、主操作だけ色を持たせる
    #    （どれも同じ濃さで並ぶと、押すべきものを毎回探すことになる）。
    # relief は "solid"。clam は "flat" だと枠を描かないので、地と地色が近い
    # このボタンは輪郭ごと消えて、押せる物に見えなくなる。
    # lightcolor/darkcolor も枠と同じ色にする（片方でも地色のままだと、その辺
    # だけ線が切れる）。押下・ホバーで**動かすのは中の色だけ**にして、輪郭は
    # 常に残す ― ここを一緒に動かすと、触った瞬間に枠が消える。
    style.configure("TButton",
                    background=p["surface"], foreground=p["text"],
                    bordercolor=p["control_edge"], lightcolor=p["control_edge"],
                    darkcolor=p["control_edge"],
                    relief="solid", borderwidth=1, padding=(10, 5))
    style.map("TButton",
              background=[("pressed", p["pressed"]), ("active", p["raised"]),
                          ("disabled", p["bg"])],
              foreground=[("disabled", p["text_faint"])],
              bordercolor=[("focus", p["accent"]), ("disabled", p["border"])],
              lightcolor=[("focus", p["accent"]), ("disabled", p["border"])],
              darkcolor=[("focus", p["accent"]), ("disabled", p["border"])])

    style.configure("Accent.TButton",
                    background=p["accent"], foreground=p["on_accent"],
                    bordercolor=p["accent"], lightcolor=p["accent"],
                    darkcolor=p["accent"], padding=(14, 6))
    style.map("Accent.TButton",
              background=[("pressed", p["accent_dim"]),
                          ("active", p["accent_lit"]),
                          ("disabled", p["accent_off"])],
              foreground=[("disabled", p["on_accent_off"])],
              lightcolor=[("pressed", p["accent_dim"]),
                          ("active", p["accent_lit"]),
                          ("disabled", p["accent_off"])],
              darkcolor=[("pressed", p["accent_dim"]),
                         ("active", p["accent_lit"]),
                         ("disabled", p["accent_off"])],
              bordercolor=[("pressed", p["accent_dim"]),
                           ("active", p["accent_lit"]),
                           ("disabled", p["accent_off"])])

    # -- 一覧。
    # 行の高さは既定だと日本語が窮屈なので広げる。
    style.configure("Treeview",
                    background=p["surface"], fieldbackground=p["surface"],
                    foreground=p["text"], bordercolor=p["border"],
                    relief="flat", rowheight=26)
    style.map("Treeview",
              background=[("selected", p["select"])],
              foreground=[("selected", p["text"])])
    style.configure("Treeview.Heading",
                    background=p["bg"], foreground=p["text_sub"],
                    relief="flat", padding=(6, 7),
                    font=(family or "", FONT_SIZE - 1, "bold"))
    style.map("Treeview.Heading",
              background=[("active", p["raised"])],
              relief=[("active", "flat"), ("pressed", "flat")])

    # -- 入力まわり。
    # 触れている欄が分かるように、焦点だけ枠を色で示す。
    # 入力欄もボタンと同じ理由で輪郭を濃くする（白い中身が地に沈む）。
    style.configure("TEntry", fieldbackground=p["surface"],
                    bordercolor=p["control_edge"],
                    lightcolor=p["control_edge"], darkcolor=p["control_edge"],
                    insertcolor=p["text"], padding=4)
    style.map("TEntry",
              bordercolor=[("focus", p["accent"])],
              lightcolor=[("focus", p["accent"])],
              darkcolor=[("focus", p["accent"])])
    style.configure("TCombobox", fieldbackground=p["surface"],
                    background=p["surface"], bordercolor=p["control_edge"],
                    lightcolor=p["control_edge"], darkcolor=p["control_edge"],
                    arrowcolor=p["text_sub"], padding=3)
    style.map("TCombobox",
              fieldbackground=[("readonly", p["surface"])],
              bordercolor=[("focus", p["accent"])],
              lightcolor=[("focus", p["accent"])],
              darkcolor=[("focus", p["accent"])])
    # チェックボタンの印は clam に描かせない。
    # clam の既定の印は、この大きさだと「入」が × に見える ― 有効にしているのに否定の記号が出るので、
    # 意味が逆に伝わる。
    # 一覧の行と**同じ絵**に差し替えて、塗りつぶしの有無で示す。
    style.configure("TCheckbutton", background=p["bg"], foreground=p["text"],
                    focuscolor=p["bg"], padding=2)
    on, off = check_images(root)
    try:
        style.element_create("Check.indicator", "image", off,
                             ("selected", on), border=0, sticky="")
    except tk.TclError:
        pass        # 既に作ってある（同じ過程を2度通った）
    try:
        style.layout("TCheckbutton", [
            ("Checkbutton.padding", {"sticky": "nswe", "children": [
                ("Check.indicator", {"side": "left", "sticky": ""}),
                ("Checkbutton.focus", {"side": "left", "sticky": "w",
                                       "children": [
                                           ("Checkbutton.label",
                                            {"sticky": "nswe"}),
                                       ]}),
            ]}),
        ])
    except tk.TclError:
        pass        # 差し替えられず。clam の既定の印のまま出る（形は悪いが動く）

    # 作業中の帯。
    # 溝を地に近い色にして、動く部分だけが目に入るようにする。
    # （clam では thickness を下げても高さは変わらないので指定しない）
    style.configure("Thin.Horizontal.TProgressbar",
                    troughcolor=p["raised"], background=p["accent"],
                    bordercolor=p["raised"], lightcolor=p["accent"],
                    darkcolor=p["accent"], borderwidth=0)

    style.configure("Vertical.TScrollbar",
                    background=p["border"], troughcolor=p["bg"],
                    bordercolor=p["bg"], arrowcolor=p["text_sub"],
                    relief="flat")
    style.map("Vertical.TScrollbar",
              background=[("pressed", p["text_faint"]),
                          ("active", p["text_faint"])])
    return style


# --------------------------------------------------------------------------
# 一覧のチェックボックス
# --------------------------------------------------------------------------
# 以前は「☑」「☐」の**文字**を一覧に流し込んでいた。
# この2つは字形が似ていて、小さい字だと ☑ の中のチェックが × に潰れる ― 有効なのに「禁止」に見えるので、
# 意味が逆に伝わる。
# 字ではなく絵にして、入/切を塗りつぶしの有無で分ける。
# 描き方は素の tk だけで済ませる（PIL を要求すると、配布物に依存が増える）。
# 背景は透明のままにする。
# 行の色で塗ると、選択された行の上で四角が浮く。


def _stroke(img: tk.PhotoImage, color: str,
            x0: float, y0: float, x1: float, y1: float, weight: int) -> None:
    """2点を結ぶ線を置く。PhotoImage には線を引く手段が無いので点で埋める。

    太さは通る点を**中心**に広げる。
    左上を起点にすると、太くするほど線全体が右下へずれる
    ― 四角の中でチェックが右下に寄って見えるのはこれが原因だった。

    刻みは長さより細かく取る。
    点を等間隔に置くだけなので、粗いと斜めの線が切れて破線に見える。
    """
    half = weight / 2.0
    span = max(abs(x1 - x0), abs(y1 - y0))
    steps = max(1, int(math.ceil(span * 3)))
    for i in range(steps + 1):
        x = math.floor(x0 + (x1 - x0) * i / steps - half + 0.5)
        y = math.floor(y0 + (y1 - y0) * i / steps - half + 0.5)
        img.put(color, to=(x, y, x + weight, y + weight))


def _rounded(img: tk.PhotoImage, color: str,
             x0: float, y0: float, x1: float, y1: float, radius: float) -> None:
    """角を丸めた四角を塗る。行ごとに横線を置いて、両端を円弧の分だけ縮める。"""
    for y in range(int(y0), int(y1)):
        dy = 0.0
        if y < y0 + radius:
            dy = (y0 + radius) - y - 0.5
        elif y >= y1 - radius:
            dy = y - (y1 - radius) + 0.5
        inset = radius - math.sqrt(max(0.0, radius * radius - dy * dy)) if dy else 0.0
        xa, xb = int(round(x0 + inset)), int(round(x1 - inset))
        if xb > xa:
            img.put(color, to=(xa, y, xb, y + 1))


def make_check_image(master: tk.Misc, size: int, on: bool) -> tk.PhotoImage:
    """入/切のチェックボックスを1枚描く。`size` は辺の画素数。

    角を丸めるのは Windows 11 の見た目に寄せるため。
    直角のままだと、同じ画面に並ぶ OS 側の部品から浮く。
    """
    p = PALETTE
    img = tk.PhotoImage(master=master, width=size, height=size)
    radius = max(2, int(round(size * 0.22)))
    if not on:
        # 枠だけ。
        # 外周を枠色で塗ってから、1px 内側を地の色で抜く。
        _rounded(img, p["check_edge"], 0, 0, size, size, radius)
        _rounded(img, p["surface"], 1, 1, size - 1, size - 1, max(1, radius - 1))
        return img
    _rounded(img, p["check"], 0, 0, size, size, radius)
    # チェックは細めに、四角の中で余白を残す形で置く。
    # 太くすると中が詰まって、印ではなく「塗り」に見える
    # ― 入っているかどうかが形から読めなくなる。
    # 16px を基準にした比率。
    # 太さは点を中心に広がる（`_stroke`）ので、
    # この3点が描く形の外接がそのまま四角の中央に来る。
    # 左右・上下とも画素 4〜11 に収まり、
    # 中心が四角の中心（0〜15 の真ん中）と一致する。
    k = size / 16.0
    weight = max(2, int(round(size / 8)))
    _stroke(img, p["on_accent"], 4.5 * k, 8.0 * k, 6.8 * k, 10.5 * k, weight)
    _stroke(img, p["on_accent"], 6.8 * k, 10.5 * k, 11.0 * k, 5.0 * k, weight)
    return img


# 作った絵はここで抱えたままにする。
# PhotoImage は Python 側の参照が切れると中身ごと消える（Tk が握るのは名前だけ）ので、
# 貼った先から絵が消える。
_CHECKS: dict[bool, tk.PhotoImage] = {}


def _image_alive(img: tk.PhotoImage, master: tk.Misc) -> bool:
    """`img` が `master` の Tk にまだ在るか。

    PhotoImage は作られた Tk インタプリタに属する。
    窓を閉じて作り直すと、Python 側の参照は残るのに中身は消えていて、
    貼ろうとした所で落ちる。
    """
    try:
        master.tk.call("image", "type", str(img))
        return True
    except tk.TclError:
        return False


def check_images(master: tk.Misc) -> tuple[tk.PhotoImage, tk.PhotoImage]:
    """（入, 切）を返す。1度作って使い回す。

    一覧の行と、mod ごとの設定ウィンドウの両方がこれを使う。
    同じ「入」が場所によって違う形で出ると、どちらかが別の意味に見える。
    """
    if _CHECKS and not _image_alive(_CHECKS[True], master):
        _CHECKS.clear()          # 別の Tk になっている。作り直す
    if not _CHECKS:
        # 画面の拡大率に合わせる。
        # 字だけ大きくなって絵が取り残されると目立つ。
        try:
            scaling = float(master.tk.call("tk", "scaling"))
        except Exception:
            scaling = 1.0
        size = max(14, int(round(12 * scaling)))
        _CHECKS[True] = make_check_image(master, size, True)
        _CHECKS[False] = make_check_image(master, size, False)
    return _CHECKS[True], _CHECKS[False]


def _read_json(path: str):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return None


# --------------------------------------------------------------------------
# mod の一覧・順序・設定
# --------------------------------------------------------------------------
# mod.json の "kind"（ローダの `KINDS`）に対応する表示名。ここに在るのは
# **表示名だけ**で、どの mod がどれに属するかは各 mod が自分の mod.json で
# 名乗る ― フォルダ名の番号帯からは導かない（帯は置き場の整理であって
# 分類の軸ではない。TECH.md §3.2.2）。
KIND_LABELS = {
    "core": "基盤",       # リコン・クラッシュ記録。他が触る前の素の状態を押さえる
    "fix": "修正",        # 既にある動作を直す・調整する
    "probe": "計測",      # 読み取り専用。値を変えない
    "feature": "追加",    # 元々無かったものを足す
}


def mod_kind(name: str, manifest: dict, local: bool = False) -> str:
    """一覧の「種別」列に出す文字。

    種別そのもの（`kind`）より、**状態を表す印を先に**見る。取込済と開発中は
    「本来の種別が何であれ、いまは普段の遊びから外れている」ことのほうが
    一覧では大事なため。`kind` はローダが語彙を検めた値（`_manifest`）。

    「ローカル」は `local/` に在る mod（配る予定が無い。TECH.md §2.6）。
    開発中と分けるのは、伏せ方が違うから ―
    あちらはデバッグモードのときだけ動くが、こちらは普段の遊びで動く。

    どれも名乗っていない mod は空 ― 無理に当てはめるより、
    規約の外に居ることがそのまま見える方が良い。
    """
    if manifest.get("superseded"):
        return "取込済"
    # 在り処が番号に勝つ。`local/` へ移した mod は 9xx の番号を残したままなので
    # （`is_wip` が真になる）、先に見ないと全部「開発中」になる。
    if local:
        return "ローカル"
    if ml.is_wip(name):
        return "開発中"
    label = KIND_LABELS.get(manifest.get("kind") or "")
    if label:
        return label
    # kind を書いていない開発者向け mod の受け皿。旗はローダが読み込みに
    # 使う値なので、名乗りが無くても計測であることだけは確か。
    if manifest.get("debug"):
        return "計測"
    return ""


def read_mods() -> dict:
    """一覧に出すものを揃えて返す。判定はローダの `discover()` に任せる。

    **無効なものも一覧には出す**（消えたように見せない）。
    適用順の番号は有効なものだけに振る。

    開発者向けの MOD（`discover()` の `debug` / `superseded`
    / 順序ファイルに載っている 9xx）も**落とさずに返す**。
    デバッグモードが切のときに描画から外すのは `_matches()` の仕事で、
    `self.mods` からは消さない ― `save()` が `self.mods` の並びをそのまま
    `order` に書き戻すので、ここで落とすと保存した瞬間に
    `load_order.json` から記述ごと消える。
    """
    found = ml.discover(MODS_DIR)
    disabled = set(found["disabled"])

    local = found.get("local") or set()
    dirs = found.get("dirs") or {}

    mods = []
    for name in found["listed"]:
        manifest = found["manifests"].get(name) or {}
        mods.append({
            "dir": name,
            # フォルダを開くのも道具を起動するのも、この実在パスから引く。
            # `MODS_DIR` を決め打つと `local/` の mod だけ見つからない。
            "path": os.path.join(dirs.get(name) or MODS_DIR, name),
            "kind": mod_kind(name, manifest, name in local),
            "name_ja": (manifest.get("name") or {}).get("ja") or name,
            "name_en": (manifest.get("name") or {}).get("en") or name,
            "desc_ja": (manifest.get("description") or {}).get("ja") or "",
            "desc_en": (manifest.get("description") or {}).get("en") or "",
            "version": manifest.get("version") or "",
            "author": manifest.get("author") or "",
            "entry": manifest.get("entry") or "mod.py",
            "api": manifest.get("api", ml.DEFAULT_API),
            "settings": manifest.get("settings") or {},
            "after": manifest.get("after") or [],
            "before": manifest.get("before") or [],
            "debug": bool(manifest.get("debug")),
            # 本体が取り込んだので降ろした mod。
            # 値は取り込まれた版（"main_024"）。
            # 読み込みの扱いは debug と同じだが、
            # 表示では分ける（伏せた理由が違う）。
            "superseded": manifest.get("superseded") or "",
            # 開発中（9xx）。
            # 読み込みの扱いは debug と同じ（デバッグモードのみ）。
            # 判定はローダの語彙（`is_wip`）を借りる ― 番号帯の規則をここに写すと、
            # 片方だけ直したとき読み込みと表示がずれる。
            # `local/` に居るものは開発中に数えない。
            # 移した MOD は 9xx の番号を残したままなので（TECH.md §2.6.2）、
            # 番号だけで見ると `_hidden()` がデバッグモードを要求してしまい、
            # 普段の遊びの一覧から消える。`discover()` の `wip` と同じ扱いに揃える。
            "wip": ml.is_wip(name) and name not in local,
            # `local/` に在る（配る予定が無い）。読み込みの条件は順序ファイルの
            # 記載だけで、デバッグモードは要らない。
            "local": name in local,
            # MOD 同梱の道具（`323_` §4）。GUI はボタンを出して別プロセスで開くだけ。
            "tool": manifest.get("tool"),
        })
    return {"mods": mods, "disabled": disabled, "problems": found["problems"],
            "debug_mode": bool(found.get("debug_mode"))}


def write_order(names: list[str], disabled: set[str]) -> None:
    """順序と無効一覧を書き戻す。

    `disabled` は `names` の並びで書く。
    差分を見たときに一覧と同じ順で並ぶ方が追いやすいのと、
    無効にした順で溜まっていくのを避けるため。
    """
    off = [n for n in names if n in disabled]
    # 落ちても壊れない書き方は `ml.write_json` に一本化してある。
    # ここは MOD の構成そのもの（適用順と有効/無効）なので、
    # 壊れると一覧が組み直せない。
    # 失敗は例外にする ― `save()` が捕まえてダイアログに出す（config.py の
    # `_save_settings_json` と同じ理由で、GUI の操作の結果だから）。
    path = ml.order_path(MODS_DIR)
    if not ml.write_json(path, {"order": names, "disabled": off}, indent=2):
        raise OSError("cannot write {}".format(path))


def read_config() -> dict:
    data = _read_json(CONFIG_PATH)
    return data if isinstance(data, dict) else {}


def write_config(data: dict) -> None:
    """`gui.json`（窓の位置など GUI だけの覚え書き）を書く。

    失っても既定値で開き直せるだけなので、書けなくても黙って続ける。
    書き方は他と揃える（規則は `ml.write_json` に一本化してある）。
    """
    os.makedirs(SETTINGS_DIR, exist_ok=True)
    ml.write_json(CONFIG_PATH, data, indent=2)


def update_config(**values) -> None:
    """`settings/gui.json` の一部だけ書き換える。

    覚えていることが増えても（窓の位置・ゲームの場所…）互いを消さないように、
    必ず読んでから書く。
    **書けなくても止めない** ― 設定を残せないことと、
    ウィンドウが使えないことは別の話なので。
    """
    try:
        cfg = read_config()
        cfg.update(values)
        write_config(cfg)
    except Exception:
        pass


# --------------------------------------------------------------------------
# ウィンドウの大きさと位置
# --------------------------------------------------------------------------
# 初めて開くときの大きさ。
# 一覧 15 行と、MOD を選んだときに伸びる説明欄（最大 5 行）と、
# 足元のボタンが全部入る高さを取る。
# 1000x620 では、選んだ瞬間に説明が伸びて一覧が潰れ、窓の下が詰まっていた。
GEOMETRY_DEFAULT = "1180x760"


def _on_screen(root: tk.Misc, geom: str) -> bool:
    """`WxH+X+Y` が画面の中に居るか。

    覚えた位置をそのまま使うと、
    モニタを外した後などに**画面の外へ出て掴めない窓**になる。
    左上が仮想デスクトップの内側にあることだけ確かめて、
    外れていたら位置は捨てて大きさだけ使う。
    """
    try:
        size, x, y = geom.replace("+-", "+~").split("+")
        x, y = int(x.replace("~", "-")), int(y.replace("~", "-"))
        w, h = (int(v) for v in size.split("x"))
    except Exception:
        return False
    # 仮想デスクトップ（マルチモニタ全体）の範囲。
    # 左と上は負にもなりうる。
    vw, vh = root.winfo_vrootwidth(), root.winfo_vrootheight()
    vx, vy = root.winfo_vrootx(), root.winfo_vrooty()
    # タイトルバーを掴める程度に見えていれば良しとする。
    return (vx - w + 120 <= x <= vx + vw - 120) and (vy <= y <= vy + vh - 60)


def restore_geometry(root: tk.Tk, cfg: dict) -> None:
    """前回の大きさと位置に戻す。壊れた値なら既定で開く。"""
    geom = cfg.get("window")
    if isinstance(geom, str) and "x" in geom:
        root.geometry(geom if _on_screen(root, geom) else geom.split("+")[0])
    else:
        root.geometry(GEOMETRY_DEFAULT)
    if cfg.get("window_maximized"):
        root.state("zoomed")


def read_status() -> dict:
    """`out/status.json` を読む。無ければ空。

    これがこのウィンドウと「実際に動いたゲーム」の唯一の接点。
    ローダが boot の最後に書き出す（`instantale_modloader.write_status`）。
    注入が成功したかどうかと **mod が入ったかどうかは別の話**なので、
    ここを読まないと「28個中3個が失敗」を画面に出せない。
    """
    data = _read_json(STATUS_PATH)
    return data if isinstance(data, dict) else {}


# --------------------------------------------------------------------------
# mod の追加
# --------------------------------------------------------------------------
def install_from_zip(zip_path: str) -> list[str]:
    """zip を `runtime/mods/` へ展開する。入っている mod のフォルダ名を返す。

    `mod.json` を持つフォルダを mod とみなす（ローダの探索規則と同じ）。
    zip の作り方は2通りあるので両方受ける。

        mymod.zip/mod.json              中身だけを固めたもの → zip 名をフォルダ名にする
        mymod.zip/mymod/mod.json        フォルダごと固めたもの → その名前を使う

    展開先を絶対パスで検査してから書く。
    zip の中の名前は信用できない（`../` を含む細工で mods/ の外へ書ける）。
    """
    with zipfile.ZipFile(zip_path) as zf:
        names = [n for n in zf.namelist() if not n.endswith("/")]
        if not names:
            raise ValueError("zip ファイルが空です")

        # mod.json の位置から、mod のフォルダがどこかを決める。
        roots = set()
        for name in names:
            parts = name.replace("\\", "/").split("/")
            if parts[-1] != ml.MANIFEST_NAME:
                continue
            roots.add("/".join(parts[:-1]))
        if not roots:
            raise ValueError("{} が見つかりません（MOD の zip ファイルではありません）".format(
                ml.MANIFEST_NAME))

        installed = []
        for root in sorted(roots):
            if root == "":
                # 中身だけの zip。
                # zip のファイル名をフォルダ名にする。
                folder = os.path.splitext(os.path.basename(zip_path))[0]
            else:
                folder = root.split("/")[-1]
            dest = os.path.abspath(os.path.join(MODS_DIR, folder))
            if not dest.startswith(os.path.abspath(MODS_DIR) + os.sep):
                raise ValueError("展開先が mods/ の外を指しています: {}".format(folder))

            prefix = (root + "/") if root else ""
            members = [n for n in names if n.replace("\\", "/").startswith(prefix)]
            for member in members:
                rel = member.replace("\\", "/")[len(prefix):]
                if not rel or rel.startswith("../") or ".." in rel.split("/"):
                    continue
                target = os.path.abspath(os.path.join(dest, rel))
                if not target.startswith(dest + os.sep) and target != dest:
                    continue
                os.makedirs(os.path.dirname(target), exist_ok=True)
                with zf.open(member) as src, open(target, "wb") as out:
                    shutil.copyfileobj(src, out)
            installed.append(folder)
        return installed


def install_from_folder(src: str) -> str:
    """フォルダを `runtime/mods/` へ複製する。フォルダ名を返す。"""
    src = os.path.abspath(src)
    if not os.path.isfile(os.path.join(src, ml.MANIFEST_NAME)):
        raise ValueError("{} がありません（MOD のフォルダではありません）".format(
            ml.MANIFEST_NAME))
    folder = os.path.basename(src.rstrip(os.sep))
    dest = os.path.join(MODS_DIR, folder)
    if os.path.abspath(dest) == src:
        return folder      # もう mods/ の中にある
    shutil.copytree(src, dest, dirs_exist_ok=True)
    return folder


# --------------------------------------------------------------------------
# 設定のウィンドウ
# --------------------------------------------------------------------------
class SettingsDialog(tk.Toplevel):
    """1つの mod の設定を編集する。宣言（`mod.json` の "settings"）に従って組む。

    既定と同じ値は**書かない**。
    `mod_settings.json` に残すのは変えた値だけで、
    そうしておくと mod の既定値が新しい版で変わったときにその変更が届く（全部書き出すと、
    既定を上書きし続ける形になって永久に古い値で動く）。

    窓の幅と位置は覚える（`settings/gui.json` の `settings_window`）。
    **mod ごとではなく1つ**で、どの mod の設定を開いても同じ場所に出る
    ― 設定を見て回るときに毎回同じ所に出る方が追いやすい。
    高さは覚えない。
    設定の数で変わるので、覚えた高さを当てると項目が切れるか余白が空く。

    項目が画面に入り切らない
    mod があるので、**設定の並びだけを巻き取り可能**にしてある（ボタンの列は下に据え置き）。
    以前は中身の高さをそのまま窓の高さにしていたので、
    項目が多いと OK ボタンごと画面の外へ出て、設定を保存できなかった。
    巻き取りが出るのは入り切らないときだけで、収まる mod の見え方は変わらない。
    """

    def __init__(self, master: tk.Misc, mod: dict, chosen: dict):
        super().__init__(master)
        self.title("{} の設定".format(mod["name_ja"]))
        # Toplevel は素の tk widget なので、
        # ttk の
        # style ではなく直に地の色を渡す（渡さないと本体の窓と違う灰色が出る）。
        self.configure(background=PALETTE["bg"])
        self.transient(master)
        self.resizable(True, True)
        self.mod = mod
        self.result: dict | None = None
        self.vars: dict[str, tuple[dict, tk.Variable]] = {}

        outer = ttk.Frame(self, padding=12)
        outer.pack(fill="both", expand=True)
        outer.rowconfigure(0, weight=1)
        outer.columnconfigure(0, weight=1)

        # 設定の並びは Canvas に載せて巻き取れるようにする。
        # ボタンの列は `outer` の下の段に置くので、
        # どれだけ項目があっても画面から出ない。
        self.canvas = tk.Canvas(outer, borderwidth=0, highlightthickness=0,
                                background=PALETTE["bg"])
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.scroll = ttk.Scrollbar(outer, orient="vertical",
                                    command=self.canvas.yview,
                                    style="Vertical.TScrollbar")
        self.canvas.configure(yscrollcommand=self.scroll.set)

        frame = ttk.Frame(self.canvas)
        self.body = frame
        self.body_id = self.canvas.create_window((0, 0), window=frame, anchor="nw")
        # 中身の幅は Canvas に合わせる（合わせないと説明の折り返しと入力欄の伸縮が効かず、
        # 窓を広げても左端に寄ったままになる）。
        self.canvas.bind(
            "<Configure>",
            lambda e: self.canvas.itemconfigure(self.body_id, width=e.width))
        frame.bind(
            "<Configure>",
            lambda _e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))

        ttk.Label(frame, text=mod["dir"], style="Sub.TLabel").grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 8))

        row = 1
        for name, decl in mod["settings"].items():
            value = chosen.get(name, decl["default"])
            ttk.Label(frame, text=decl["label"]["ja"]).grid(
                row=row, column=0, sticky="w", padx=(0, 10), pady=3)
            widget, var = self._field(frame, decl, value)
            widget.grid(row=row, column=1, sticky="ew", pady=3)
            self.vars[name] = (decl, var)
            row += 1
            note = decl["note"]["ja"]
            if note:
                ttk.Label(frame, text=note, style="Sub.TLabel",
                          wraplength=420, justify="left").grid(
                    row=row, column=1, sticky="w", pady=(0, 6))
                row += 1
            # 既定値を必ず見せる。
            # 「元に戻したい」ときに何に戻すのかが分かるように。
            ttk.Label(frame, text="既定: {!r}".format(decl["default"]),
                      style="Faint.TLabel").grid(row=row, column=1, sticky="w")
            row += 1

        frame.columnconfigure(1, weight=1)
        bar = ttk.Frame(outer)
        bar.grid(row=1, column=0, columnspan=2, sticky="e", pady=(12, 0))
        ttk.Button(bar, text="既定に戻す", command=self._reset).pack(side="left", padx=4)
        ttk.Button(bar, text="キャンセル", command=self._close).pack(side="left", padx=4)
        ttk.Button(bar, text="OK", command=self._ok).pack(side="left")

        self.bind("<MouseWheel>", self._on_wheel)
        self.bind("<Escape>", lambda _e: self._close())
        self.protocol("WM_DELETE_WINDOW", self._close)
        self._restore()
        self.grab_set()
        self.wait_window()

    # -- 窓の幅と位置 --------------------------------------------------------
    def _restore(self) -> None:
        geom = read_config().get("settings_window")
        # 中身を組み終えてからでないと、必要な寸法が分からない。
        height = self._fit_height()
        width = self.winfo_reqwidth()

        remembered = isinstance(geom, str) and "x" in geom
        if remembered:
            try:
                width = max(int(geom.split("x")[0]), width)
            except ValueError:
                pass

        # 覚えていなければ置き場所は Tk に任せる（大きさだけ指定する）。
        pos = ""
        if remembered:
            if "+" in geom and _on_screen(self, geom):
                pos = geom[geom.index("+"):]
            else:
                # 覚えた位置が画面の外。
                # 大きさだけ指定すると画面の左上に張り付くので、
                # 呼び出し元の窓に重ねる（Tk が transient に選ぶのと同じ）。
                master = self.master
                pos = "+{}+{}".format(master.winfo_rootx() + 60,
                                      master.winfo_rooty() + 60)
        self.geometry("{}x{}{}".format(width, height, pos))

    def _fit_height(self) -> int:
        """窓の高さを返し、入り切らなければ巻き取りを出す。

        Canvas は中の物に合わせて伸びないので、こちらから要求する高さを渡す。
        そうしないと「中身が収まる mod」でも
        Tk の既定の高さ（中身とは無関係）が窓の高さになる。
        """
        self.update_idletasks()
        content = self.body.winfo_reqheight()
        self.canvas.configure(width=self.body.winfo_reqwidth(), height=content)
        self.update_idletasks()

        natural = self.winfo_reqheight()        # 余白 + 中身 + ボタンの列
        limit = int(self.winfo_screenheight() * 0.85)
        if natural <= limit:
            self.scroll.grid_remove()
            return natural
        self.scroll.grid(row=0, column=1, sticky="ns", padx=(6, 0))
        # 窓を上限まで詰めたぶんだけ、見える中身も縮む。
        self.canvas.configure(height=max(content - (natural - limit), 80))
        return limit

    def _on_wheel(self, event: tk.Event) -> None:
        """巻き取りが出ているときだけホイールを効かせる。"""
        if not self.scroll.winfo_ismapped():
            return
        self.canvas.yview_scroll(-3 if event.delta > 0 else 3, "units")

    def _close(self) -> None:
        update_config(settings_window=self.geometry())
        self.destroy()

    def _field(self, parent, decl, value):
        kind = decl["type"]
        if kind == "bool":
            var = tk.BooleanVar(value=bool(value))
            return ttk.Checkbutton(parent, variable=var), var
        if kind == "choice":
            var = tk.StringVar(value="" if value is None else str(value))
            box = ttk.Combobox(parent, textvariable=var, state="readonly",
                               values=[str(v) for v in decl["values"]])
            return box, var
        # int / float / str。
        # int / float の空欄は「未指定」（allow_null の設定で許される）。
        # str の空欄は**空文字列という値**（`_ok` を参照）。
        var = tk.StringVar(value="" if value is None else str(value))
        return ttk.Entry(parent, textvariable=var), var

    def _reset(self) -> None:
        for name, (decl, var) in self.vars.items():
            default = decl["default"]
            if decl["type"] == "bool":
                var.set(bool(default))
            else:
                var.set("" if default is None else str(default))

    def _ok(self) -> None:
        chosen, bad = {}, []
        for name, (decl, var) in self.vars.items():
            raw = var.get()
            if isinstance(raw, str) and raw.strip() == "" \
                    and decl["type"] != "str":
                # 空欄 = 未指定。
                # ただし str では**空文字列そのものが値**（「空でゲームのまま」のような設定がある。
                # `314_` の文言）。
                # None に変えると coerce が「null は許されていない」で弾き、
                # 既定が空の欄は未編集で OK を押しただけでエラーになる。
                raw = None
            ok, value, why = C.coerce(decl, raw)
            if not ok:
                bad.append("{}: {}".format(decl["label"]["ja"], why))
                continue
            # 既定と同じなら書かない（上記の docstring 参照）。
            if value != decl["default"]:
                chosen[name] = value
        if bad:
            messagebox.showerror("値が不正です", "\n".join(bad), parent=self)
            return
        self.result = chosen
        self._close()


# --------------------------------------------------------------------------
# 説明の吹き出し
# --------------------------------------------------------------------------
class Tooltip:
    """widget に説明を出す。

    一覧の「有効」「設定」の列は記号しか出ていない（チェック、● と ○）。
    何を表しているかも、押せることも、触ってみるまで判らない。
    列の意味を見出しに全部書くと幅が要るので、指した所だけ出す。

    `text` には文字列のほか、event を受けて文字列を返す関数を渡せる。
    列ごとに中身を変えるため ― 出す物が変わったら出し直し、空を返したらしまう。
    """

    DELAY = 550          # ms。動かしている最中に次々出さないための間

    def __init__(self, widget: tk.Misc, text) -> None:
        self.widget = widget
        self.text = text
        self.tip: tk.Toplevel | None = None
        self.pending: str | None = None
        self.showing = ""
        # add="+" で足す。
        # この widget には既に別の用途の割り当てがある（一覧のドラッグなど）ので、
        # 置き換えると壊れる。
        widget.bind("<Motion>", self._on_motion, add="+")
        widget.bind("<Leave>", lambda _e: self._hide(), add="+")
        widget.bind("<Button-1>", lambda _e: self._hide(), add="+")
        widget.bind("<Button-3>", lambda _e: self._hide(), add="+")

    def _resolve(self, event: tk.Event) -> str:
        if callable(self.text):
            try:
                return self.text(event) or ""
            except Exception:
                return ""       # 説明が出ないことと、一覧が使えないことは別の話
        return str(self.text)

    def _on_motion(self, event: tk.Event) -> None:
        text = self._resolve(event)
        if text == self.showing:
            return              # 同じ物の上を動いているだけ。出し直さない
        self._hide()
        if not text:
            return
        x, y = event.x_root + 16, event.y_root + 22
        self.pending = self.widget.after(
            self.DELAY, lambda: self._show(text, x, y))

    def _show(self, text: str, x: int, y: int) -> None:
        self.pending = None
        tip = tk.Toplevel(self.widget)
        tip.wm_overrideredirect(True)            # 枠もタイトルも出さない
        tip.configure(background=PALETTE["border"])
        tk.Label(tip, text=text, justify="left", bd=0, padx=8, pady=5,
                 background=PALETTE["surface"],
                 foreground=PALETTE["text"]).pack(padx=1, pady=1)
        tip.wm_geometry("+{}+{}".format(x, y))
        self.tip = tip
        self.showing = text

    def _hide(self) -> None:
        if self.pending is not None:
            self.widget.after_cancel(self.pending)
            self.pending = None
        if self.tip is not None:
            self.tip.destroy()
            self.tip = None
        self.showing = ""


# --------------------------------------------------------------------------
# GUI 本体
# --------------------------------------------------------------------------
class App(ttk.Frame):
    # (キー, 見出し, 幅, 寄せ, 伸ばすか)「有効」はここに無い。
    # チェックは絵なので、Treeview で絵を置ける唯一の列（一番左の #0）に入れる。
    COLUMNS = (
        ("order", "順", 40, "center", False),
        ("kind", "種別", 56, "center", False),
        ("name_ja", "Name（日本語）", 200, "w", True),
        # 英名（mod.json の name.en）ではなくフォルダ名を出す。ログ・
        # load_order.json・`after` の宣言はどれもフォルダ名で書かれるので、
        # 一覧と突き合わせるときはこちらのほうが役に立つ。英名は検索
        # （`_matches`）と mod.json には残っている。
        ("dir", "フォルダ名", 200, "w", True),
        ("cfg", "設定", 44, "center", False),
        ("version", "Ver", 46, "center", False),
        ("author", "Author", 120, "w", False),
        ("state", "前回", 76, "center", False),
    )

    # 列に指したときに出す説明。
    # 記号だけの列（有効・設定）は、意味と「押せる」ことの両方を書く
    # ― 見出しの文字数では入りきらない。
    COLUMN_HELP = {
        "on": "この MOD を適用するかどうか。クリックで切り替え（Space キーでも同じ）",
        "order": "上から適用される順番。無効なものには番号を振りません",
        "kind": "修正 = 本体の挙動を直す ／ 計測 = 読み取り専用の記録（開発者向け）\n"
                "追加 = 新しい機能 ／ 取込済 = 本体が同じ修正を取り込んだもの\n"
                "基盤 = 他より先に動く土台 ／ 開発中 = 未公開（手元だけ）",
        "name_ja": "行をドラッグすると適用順を変えられます",
        "dir": "MOD の実体（runtime\\mods\\ のフォルダ名）。ログや load_order.json は\n"
               "この名前で書かれます。行をドラッグすると適用順を変えられます",
        "cfg": "● = 既定から変更あり ／ ○ = 既定のまま。クリックで設定を開きます\n"
               "（印が無い MOD は、変更できる設定を持っていません）",
        "version": "mod.json に書かれた版",
        "author": "mod.json に書かれた作者",
        "state": "前回の注入の結果。「適用」以外だった行は赤で出ます",
    }

    # 絞り込みの種類。
    # 並び順は「よく使う順」。
    FILTERS = ("すべて", "有効のみ", "無効のみ", "前回失敗", "設定あり")

    def __init__(self, master: tk.Tk) -> None:
        super().__init__(master, padding=10)
        self.pack(fill="both", expand=True)

        self.mods: list[dict] = []
        self.disabled: set[str] = set()
        self.problems: list[str] = []
        self.settings: dict = {}          # mod_settings.json の中身
        self.status: dict = {}            # out/status.json の中身
        self.drag: str | None = None      # ドラッグ中の mod のフォルダ名
        self.dirty = False
        self.busy = False
        self.update: tuple[str, str] | None = None   # 新しい版（版, full zip の URL）
        self._status_text = ""            # 状態表示に今出ている文言
        self.shown_count = 0              # 絞り込みの結果、一覧に出ている数
        # デバッグモード。
        # 切のあいだ、開発者向けの MOD は一覧に出ないし読み込まれもしない。
        # 実体は settings/loader.json で、ローダも同じ値を読む。
        self.debug_mode = False
        # 絞り込みの条件。
        # widget より先に作る（`_build` の中で参照する）。
        self.query = tk.StringVar()
        self.filter_mode = tk.StringVar(value=self.FILTERS[0])
        # メニューのチェックの見た目。
        # 実体は `self.debug_mode` の方で、こちらは表示専用（`reload` が毎回
        # `set` で揃える）。
        self.debug_var = tk.BooleanVar(value=False)
        # ログの世代管理。
        # こちらは GUI 側に控えを持たず、チェックの状態がそのまま今の値になる（`reload` が
        # logrotate に聞いて揃える）。
        # 実体は debug と同じ
        # settings/loader.json だが、**読むのはローダではなく注入する側**（tools/logrotate.py）。
        self.log_rotate_var = tk.BooleanVar(value=True)
        # 注入は別スレッドで動くので、進捗はキュー越しに受け取ってメインスレッドの
        # after で描く（tkinter は他スレッドから触れない）。
        self.events: queue.Queue[tuple[str, str]] = queue.Queue()
        # 最大化していない状態の大きさと位置。
        # 最大化中の値を覚えると、
        # 次に開いたときに画面いっぱいの「普通の窓」になってしまうので分けて持つ。
        self.geom = master.geometry()

        self._build()
        self.reload()
        threading.Thread(target=self._check_update, daemon=True).start()
        # 進捗を拾う繰り返し。
        # 閉じるときに止める（止めないと、消えた widget を相手に1回だけ走って
        # Tk がエラーを吐く）。
        self._tick = self.after(100, self._drain_events)

        master.bind("<Configure>", self._on_configure)
        master.protocol("WM_DELETE_WINDOW", self._on_close)

    # -- 組み立て ----------------------------------------------------------
    def _build(self) -> None:
        self._build_menu()

        head = ttk.Frame(self)
        head.pack(fill="x")
        ttk.Label(head, text="Instantale ModLoader",
                  style="Title.TLabel").pack(side="left")
        ttk.Label(head, text="上から順に適用されます（行をドラッグして並べ替え）",
                  style="Sub.TLabel").pack(side="left", padx=(12, 0))
        # 新しい版が在るときだけ pack する（`_drain_events` の "update"）。
        self.update_btn = ttk.Button(head, command=self._update)

        # 絞り込み。
        # MOD が 30 個を超えると、上から目で追うしか無くなる。
        filters = ttk.Frame(self)
        filters.pack(fill="x", pady=(10, 0))
        ttk.Label(filters, text="検索", style="Sub.TLabel").pack(side="left")
        entry = ttk.Entry(filters, textvariable=self.query, width=28)
        entry.pack(side="left", padx=(6, 0))
        entry.bind("<Escape>", lambda _e: self.clear_filter())
        self.search_entry = entry
        ttk.Label(filters, text="表示", style="Sub.TLabel").pack(
            side="left", padx=(16, 0))
        ttk.Combobox(filters, textvariable=self.filter_mode, state="readonly",
                     values=list(self.FILTERS), width=10).pack(
            side="left", padx=(6, 0))
        ttk.Button(filters, text="解除", width=6,
                   command=self.clear_filter).pack(side="left", padx=(8, 0))
        self.count_label = ttk.Label(filters, text="", style="Faint.TLabel")
        self.count_label.pack(side="left", padx=(12, 0))
        # 条件が変わったら組み直す。
        # 入力のたびに走るが、30 個程度なら気にならない。
        self.query.trace_add("write", self._on_filter_change)
        self.filter_mode.trace_add("write", self._on_filter_change)

        # body は作るだけで、置くのは最後。
        # pack は先に置いた物から場所を配り、
        # 足りなくなった分は後ろの物から削られる ― 一覧を先に置くと、
        # 窓が低いときに**足元のボタンごと消える**（MOD を選んで説明が伸びた瞬間に、
        # 起動ボタンが画面の外に出ていたのはこれ）。
        # 下に付く物を先に確保して、余りを一覧に渡す。
        # 一覧と MOD 情報を左右に分ける。境目はドラッグで動かせる。
        # 説明の量は mod によって 3 倍以上ちがう（実測 55〜293px）ので、
        # どこかに決め打ちの高さを置くより、見る側で配分を決められる方がいい。
        self.panes = ttk.PanedWindow(self, orient="horizontal")
        body = ttk.Frame(self.panes, width=self.LIST_MIN)
        # 中身の大きさを外へ伝えない。Treeview は「いまの列幅の合計」を自分の
        # 要求幅として出すので、枠が広がって列が伸びると**要求幅まで一緒に
        # 増える**。PanedWindow は要求幅より狭くは畳まないため、一度広げると
        # 境目が左へ戻せなくなり、右の枠を広げられなくなる（実測：右が 378px
        # で頭打ちになった）。ここで切っておけば、幅は境目だけが決める。
        body.pack_propagate(False)

        # `show` に "tree" を含めるのは、#0 の列にチェックの絵を置くため（"headings" だけだと #0 が隠れて、
        # 絵を出す場所が無くなる）。
        self.check_on, self.check_off = check_images(self)
        self.tree = ttk.Treeview(body, columns=[c[0] for c in self.COLUMNS],
                                 show="tree headings", selectmode="browse",
                                 height=15)
        self.tree.heading("#0", text="有効", anchor="center")
        self.tree.column("#0", width=52, minwidth=52, stretch=False,
                         anchor="center")
        for key, title, width, anchor, stretch in self.COLUMNS:
            # 見出しは中身と同じ側に寄せる。
            # ttk の既定は中央なので、左寄せの列（名前・作者）で見出しだけが中に浮いて、
            # 列の切れ目が読めなくなる。
            self.tree.heading(key, text=title, anchor=anchor)
            self.tree.column(key, width=width, anchor=anchor, stretch=stretch)
        # 無効な行は灰色にする。
        # チェックだけだと、一覧を眺めたときに「効いていない
        # mod がある」ことに気付きにくい。
        self.tree.tag_configure("off", foreground=PALETTE["text_faint"])
        # 前回の注入で入らなかった mod は赤。
        # ログを開かずに気付けるように。
        self.tree.tag_configure("bad", foreground=PALETTE["danger"])
        # 本体が取り込んだので降ろした mod。
        # デバッグモードのときだけ並ぶので、**計測 MOD と一緒に見えることになる**。
        # 同じ見た目だと「なぜ出ているのか」が混ざるため、
        # こちらは文字色も変える（無効な行の灰色とも別にする ― 切ってあるのではなく、
        # 要らなくなったので降ろした、という違いがある）。地は下の `taken`。
        self.tree.tag_configure("superseded", foreground=PALETTE["text_sub"])
        # 背景は文字色とは**別の軸**で、同じ行に重なる
        # （無効にした計測 MOD は灰字＋計測の地）。
        # 選択中は style.map の選択色が勝つ ― off /
        # bad の文字色が選択で消えるのと同じ振る舞いに揃う。
        #
        # 伏せる理由ごとに地の色を分ける。
        # デバッグモードを入れると計測・開発中・取込済が同時に並ぶので、
        # 1色でまとめると「なぜ出ているのか」が読めない。
        self.tree.tag_configure("dev", background=PALETTE["dev_bg"])
        self.tree.tag_configure("wip", background=PALETTE["wip_bg"])
        self.tree.tag_configure("taken", background=PALETTE["taken_bg"])
        # `local/` から読んだ行（配る予定が無い MOD。TECH.md §2.6.2）。
        # `dev` と同じ「背景の軸」だが、こちらは**デバッグモードに関係なく常に並ぶ**。
        # 普段の遊びの一覧に混ざるので、配布物に入るものと地の色で分かれている必要がある。
        self.tree.tag_configure("local", background=PALETTE["local_bg"])
        self.tree.bind("<<TreeviewSelect>>", lambda _e: self._show_detail())
        self.tree.bind("<Button-1>", self._on_press)
        self.tree.bind("<B1-Motion>", self._on_drag)
        self.tree.bind("<ButtonRelease-1>", self._on_release)
        self.tree.bind("<space>", lambda _e: self._toggle())
        self.tree.bind("<Double-1>", self._on_double)
        self.tree.bind("<Return>", lambda _e: self._edit_settings())
        self.tree.bind("<Button-3>", self._on_context)
        self._build_row_menu()
        self.tree_tip = Tooltip(self.tree, self._tip_for)

        bar = ttk.Scrollbar(body, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=bar.set)

        # 置く順で、幅が足りないときに何を守るかが決まる。スクロールバーを
        # 先に確保し、残りを一覧に渡す。
        bar.pack(side="right", fill="y")
        self.tree.pack(side="left", fill="both", expand=True)

        self._build_info(self.panes)

        # 宣言と実体のずれ（順序・依存・非互換）。
        # これは**一覧ぜんぶに関わる**話で、選んだ mod の話ではないので、
        # 右の MOD 情報ではなく窓の下に置いたままにする。
        # 何も無ければ行ごと出さない。
        self.warn = ttk.Label(self, text="", style="Warn.TLabel",
                              wraplength=860, justify="left")

        foot = ttk.Frame(self)
        # このウィンドウで押すものの中で、これだけが「実際にゲームが変わる」操作。
        # 他と同じ濃さで並べると毎回探すことになるので、
        # 色を持たせて1つだけ立たせる。
        self.launch_btn = ttk.Button(foot, text="MOD を注入してゲームを起動",
                                     style="Accent.TButton", command=self.launch)
        self.launch_btn.pack(side="left")
        self.unload_btn = ttk.Button(foot, text="MOD を外す", command=self.unload)
        self.unload_btn.pack(side="left", padx=6)
        # 保存は一覧の脇ではなく起動の隣に置く。
        # 「並べ替えて、保存して、起動する」が一続きの流れなので、
        # 最後の2つが同じ場所にある方が追える。
        # 押せるかどうかが未保存の有無をそのまま表す（`_update_actions`）。
        self.save_btn = ttk.Button(foot, text="保存", command=self.save)
        self.save_btn.pack(side="left")
        self.status_label = ttk.Label(foot, text="", style="Sub.TLabel")
        self.status_label.pack(side="left", padx=10)
        # 作業中の帯。
        # 動いていないときは出さない。
        # 幅は決め打ちで短くする。
        # clam の動く部分は溝の長さに関わらず 30px 弱にしかならないので、
        # 行いっぱいに伸ばすと、長い溝の上を小さな点が滑るだけになって、
        # 動いていることが伝わらない。
        self.progress = ttk.Progressbar(foot, mode="indeterminate", length=180,
                                        style="Thin.Horizontal.TProgressbar")

        # ここで置く順が、窓が足りないときに何を守るかを決める。
        # 下から順に確保して、最後に残りを一覧へ渡す。
        # 一覧は縮んでも行が減るだけだが、ボタンは消えると押せなくなる。
        foot.pack(side="bottom", fill="x", pady=(6, 0))
        self.warn.pack(side="bottom", fill="x")
        self.panes.pack(side="top", fill="both", expand=True, pady=(8, 0))
        self.panes.add(body, weight=3)
        self.panes.add(self.info, weight=1)
        # 境目の位置は窓が出来てから入れる。この時点の幅はまだ 1 なので、
        # いま sashpos を呼んでも捨てられる。
        self.after_idle(self._restore_sash)

    # -- MOD 情報（右の枠）--------------------------------------------------
    INFO_WIDTH = 430          # 初めて開いたときの幅
    INFO_MIN = 260            # これより狭いと中身が読めなくなる
    LIST_MIN = 420            # 一覧側にも同じだけ最低限を残す

    def _build_info(self, parent) -> None:
        """選んでいる mod の情報を出す枠。

        以前は窓の下いっぱいに帯で出していた。説明の長さが mod ごとに 3 倍以上
        ちがう（実測 55〜293px）ので、選び直すたびに一覧の高さが変わり、次に
        押そうとした行が動いていた。縦に置けば、説明が伸びても一覧は動かない。
        """
        self.info = ttk.Frame(parent, padding=(12, 0, 0, 0))
        info = self.info

        # 一番よく押すものを一番上の一行にまとめる。切り替えと並べ替えは
        # どちらも「選んでいる MOD をどう扱うか」なので、離して置くと目と
        # 手が行き来する。
        top = ttk.Frame(info)
        top.pack(fill="x")
        self.info_toggle = ttk.Button(top, text="有効 / 無効", width=11,
                                      command=self._toggle)
        self.info_toggle.pack(side="left")
        ttk.Label(top, text="位置", style="Group.TLabel").pack(
            side="left", padx=(14, 5))
        self.info_move = []
        # 横に並べるので字は矢印だけにする。何をするかは指せば出る
        # （`Tooltip`）。同じ操作はメニューにも語で載っている。
        for label, tip, command in (
                ("▲", "1つ上へ", lambda: self._move(-1)),
                ("▼", "1つ下へ", lambda: self._move(1)),
                ("⤒", "一番上へ", lambda: self._move_to(0)),
                ("⤓", "一番下へ", lambda: self._move_to(-1))):
            button = ttk.Button(top, text=label, width=3, command=command)
            button.pack(side="left", padx=1)
            Tooltip(button, tip)
            self.info_move.append(button)

        self.info_name = ttk.Label(info, text="", style="InfoName.TLabel",
                                   justify="left")
        self.info_name.pack(anchor="w", fill="x", pady=(10, 0))
        self.info_state = ttk.Label(info, text="", style="Faint.TLabel",
                                    justify="left")
        self.info_state.pack(anchor="w", fill="x")

        # フォルダ名は読み取り専用の入力欄にする。ログ・load_order.json・
        # `after` の宣言はどれもフォルダ名で書かれるので、**選んで写せる**のが
        # 効く（ラベルだと写せない）。
        ttk.Label(info, text="フォルダ名", style="Group.TLabel").pack(
            anchor="w", pady=(12, 2))
        line = ttk.Frame(info)
        line.pack(fill="x")
        self.info_dir = ttk.Entry(line, state="readonly")
        self.info_dir.pack(side="left", fill="x", expand=True)
        self.info_open = ttk.Button(line, text="開く", width=5,
                                    command=self.open_mod_dir)
        self.info_open.pack(side="left", padx=(6, 0))

        # 「設定…」は1つ。`mod.json` に "tool" を宣言した MOD では、
        # 宣言の設定ダイアログではなく同梱の設定画面を開く（`_edit_settings`）。
        self.info_cfg = ttk.Button(info, text="設定…", width=12,
                                   command=self._edit_settings)
        self.info_cfg.pack(anchor="w", pady=(12, 0))

        # 出していない操作の在り処。減らしただけだと「できなくなった」
        # ように見える。折返し幅は枠に追従させる（`_build_info` の外で
        # 幅が決まるので、決め打ちにすると狭いときに右端が切れる）。
        self.info_hint = ttk.Label(
            info, text="そのほかの操作は、一覧の行を右クリック、または上のメニュー",
            style="Faint.TLabel", justify="left")
        self.info_hint.pack(anchor="w", fill="x", pady=(8, 0))
        info.bind("<Configure>", self._fit_hint)

        meta = ttk.Frame(info)
        meta.pack(fill="x", pady=(12, 0))
        meta.columnconfigure(1, weight=1)
        self.info_meta = {}
        for row, (key, title) in enumerate((("author", "作者"),
                                            ("version", "バージョン"))):
            ttk.Label(meta, text=title, style="Group.TLabel", width=10).grid(
                row=row, column=0, sticky="w", pady=1)
            value = ttk.Label(meta, text="", style="Sub.TLabel")
            value.grid(row=row, column=1, sticky="w", pady=1)
            self.info_meta[key] = value

        head = ttk.Frame(info)
        head.pack(fill="x", pady=(14, 3))
        ttk.Label(head, text="説明", style="Group.TLabel").pack(side="left")
        # 英文は既定で畳む。説明の総量の 7 割近く（実測 9,774 / 14,273 字）が
        # 英文で、しかも和文の訳なので、常に出すと読む所を探すことになる。
        # 検索（`_matches`）の対象には残してあるので、英語の語でも見つかる。
        self.show_en = tk.BooleanVar(value=bool(read_config().get("show_en")))
        self.show_en.trace_add("write", self._on_show_en)
        ttk.Checkbutton(head, text="English", variable=self.show_en).pack(
            side="right")

        box = ttk.Frame(info)
        box.pack(fill="both", expand=True)
        # ラベルではなく Text にする。長い説明を**枠の中で**スクロールさせたい
        # ので、中身の高さで枠が伸びない入れ物が要る。width/height を 1 にして
        # おくのは、要求サイズで枠を押し広げさせないため（実寸は pack が決める）。
        # 書体は明示する。tk.Text の既定は **TkFixedFont**（Windows では
        # ＭＳ ゴシック）で、窓の他の場所とは別の書体になる ― 一番よく読む
        # 場所だけ字面が変わって、読みにくさの元になっていた。
        self.desc = tk.Text(box, wrap="word", relief="flat", bd=0,
                            width=1, height=1, padx=8, pady=6,
                            font="TkDefaultFont",
                            background=PALETTE["surface"],
                            foreground=PALETTE["text"],
                            highlightthickness=1,
                            highlightbackground=PALETTE["control_edge"],
                            highlightcolor=PALETTE["control_edge"],
                            cursor="arrow")
        self.desc.pack(side="left", fill="both", expand=True)
        scroll = ttk.Scrollbar(box, orient="vertical", command=self.desc.yview)
        scroll.pack(side="left", fill="y")
        self.desc.configure(yscrollcommand=scroll.set, state="disabled")
        # 濃さと間隔で段を分ける。全部同じ見た目だと、どこまでが説明で
        # どこからが付帯情報なのかが読めない。
        # spacing2 は「折り返した行どうし」の間。和文は字が詰まるので、
        # ここを空けると幅を変えずに読む速さが上がる。
        self.desc.tag_configure("body", spacing2=3, spacing3=8)
        self.desc.tag_configure("en", foreground=PALETTE["text_sub"],
                                spacing1=4, spacing2=3, spacing3=8)
        self.desc.tag_configure("note", foreground=PALETTE["warn"],
                                spacing2=3, spacing3=10)
        caption = tkfont.nametofont("TkDefaultFont").copy()
        caption.configure(weight="bold", size=max(7, FONT_SIZE - 1))
        self.desc.tag_configure("label", foreground=PALETTE["text_sub"],
                                font=caption, spacing1=10, spacing3=2)
        self.desc.tag_configure("meta", foreground=PALETTE["text"],
                                spacing2=3, lmargin1=10, lmargin2=10)

    def _fit_hint(self, event: tk.Event) -> None:
        """案内の折返しを枠の幅に合わせる。

        決め打ちにすると、境目を左へ寄せて枠を狭めたときに右端が切れる。
        """
        width = max(80, event.width - 24)
        if self.info_hint.cget("wraplength") != width:
            self.info_hint.configure(wraplength=width)

    def _on_show_en(self, *_args) -> None:
        """英文の出し入れ。次に開いたときも同じ状態で出す。"""
        update_config(show_en=bool(self.show_en.get()))
        self._show_detail()

    # -- 左右の配分 ----------------------------------------------------------
    def _info_width(self) -> int:
        """右の枠の幅。次に開くときに戻す（`_restore_sash`）。"""
        try:
            return max(0, self.panes.winfo_width() - self.panes.sashpos(0))
        except tk.TclError:
            return self.INFO_WIDTH

    def _restore_sash(self) -> None:
        """境目を前回の位置に戻す。窓の幅が決まってからでないと効かない。"""
        total = self.panes.winfo_width()
        if total <= 1:
            self.after(50, self._restore_sash)
            return
        want = read_config().get("info_width")
        width = want if isinstance(want, int) and want > 0 else self.INFO_WIDTH
        # 窓が狭いときは、両側に最低限を残せる範囲に丸める。覚えた幅をそのまま
        # 入れると、次に小さい画面で開いたときに一覧が潰れる。
        width = max(self.INFO_MIN, min(width, max(0, total - self.LIST_MIN)))
        try:
            self.panes.sashpos(0, max(0, total - width))
        except tk.TclError:
            pass

    # -- 絞り込み ------------------------------------------------------------
    def _hidden(self, mod: dict) -> bool:
        """デバッグモードが切のあいだ伏せる mod か。

        絞り込み（`_matches`）とは別物として扱う。
        絞り込みは今かけている条件で、解除すれば戻る。
        こちらは**存在しないものとして扱う**もので、件数の分母にも入れない。

        伏せる理由は3つあり、扱いは同じ（`discover()` の `hide`）:

          debug       計測用。開発者以外には意味が無い
          superseded  ゲーム本体が同じ修正を取り込んだので降ろした
          wip         開発中（9xx）。まだ配る形が決まっていない

        `local/` の mod はここに**入らない**（TECH.md §2.6.2）。
        配る予定が無いだけで作りかけではなく、普段の遊びで動かすために置いてある。
        番号が 9xx のままのものが在るので、`wip` の側で先に除いてある（`read_mods`）。

        どれもデバッグモードを入れれば出てくる。
        **表示だけは分ける**ので、
        ここで一緒にするのは「出すか出さないか」の判定だけに留める。
        """
        return (mod["debug"] or bool(mod.get("superseded")) or mod["wip"]) \
            and not self.debug_mode

    def _known_mods(self) -> list[dict]:
        """画面から見て「入っている」mod。件数の分母はこちら。"""
        return [m for m in self.mods if not self._hidden(m)]

    def _off_known(self) -> set[str]:
        """切られている mod のうち、一覧に出ているものだけ。

        伏せている mod の有効/無効は**画面に見えないので触らない**。
        まとめて有効にする操作がここを通ることで、見えないものまで巻き込まずに済む。
        """
        return self.disabled & {m["dir"] for m in self._known_mods()}

    def _matches(self, mod: dict) -> bool:
        """この mod を一覧に出すか。"""
        if self._hidden(mod):
            return False
        text = self.query.get().strip().lower()
        if text:
            # 説明も対象にする。
            # 「BGM」で探して、名前に入っていない mod まで見つかる方が、
            # 探している側の意図に近い。
            hay = " ".join((mod["dir"], mod["name_ja"], mod["name_en"],
                            mod["author"], mod["desc_ja"], mod["desc_en"])).lower()
            if text not in hay:
                return False
        mode = self.filter_mode.get()
        if mode == "有効のみ":
            return mod["dir"] not in self.disabled
        if mode == "無効のみ":
            return mod["dir"] in self.disabled
        if mode == "前回失敗":
            result = (self.status.get("mods") or {}).get(mod["dir"], "")
            return bool(result) and result != "ok"
        if mode == "設定あり":
            return bool(mod["settings"])
        return True

    def _visible_mods(self) -> list[dict]:
        """今その順で一覧に並んでいるものだけ。"""
        return [m for m in self.mods if self._matches(m)]

    def _filtering(self) -> bool:
        return bool(self.query.get().strip()) or self.filter_mode.get() != self.FILTERS[0]

    def _on_filter_change(self, *_args) -> None:
        keep = self.tree.selection()
        self._refresh(keep=keep[0] if keep else None)
        if self._filtering():
            self._set_status("{} 個中 {} 個を表示しています（絞り込み中）".format(
                len(self._known_mods()), self.shown_count))
        else:
            self._set_status("絞り込みを解除しました")

    def focus_search(self) -> None:
        self.search_entry.focus_set()
        self.search_entry.select_range(0, "end")

    def clear_filter(self) -> None:
        """検索語と表示条件を戻す。どちらも既定なら何もしない。"""
        if not self._filtering():
            return
        self.filter_mode.set(self.FILTERS[0])
        self.query.set("")          # 2つ目の書き換えで `_on_filter_change` が走る

    def _tip_for(self, event: tk.Event) -> str:
        """指した場所に応じた説明。列の意味は見出しだけでは足りない。"""
        region = self.tree.identify_region(event.x, event.y)
        if region not in ("tree", "cell", "heading"):
            return ""
        # チェックの列は #0。
        # cell ではなく "tree" として来るが、見出しの上では "heading" になるので、
        # 列番号でも見る。
        if region == "tree" or self.tree.identify_column(event.x) == "#0":
            return self.COLUMN_HELP["on"]
        return self.COLUMN_HELP.get(self._column_key(event.x), "")

    # -- メニュー ------------------------------------------------------------
    # 一覧の脇から外した操作の行き先。
    # ここに置くのは「たまにしか押さないが無いと困る」もの ― 出しっぱなしにすると、
    # よく使うものが埋もれる。
    # ショートカットも同じ場所で決める（メニューに出る表記と実際の割り当てが離れていると、
    # 片方だけ直したときに嘘になる）。
    MENU_KEYS = (
        ("<Control-o>", "add_mod"),
        ("<Control-s>", "save"),
        ("<Control-f>", "focus_search"),
        ("<F5>", "reload"),
        ("<F9>", "launch"),
    )

    def _build_menu(self) -> None:
        master = self.winfo_toplevel()
        bar = tk.Menu(master, tearoff=0)

        m_file = tk.Menu(bar, tearoff=0)
        m_file.add_command(label="MOD を追加…", accelerator="Ctrl+O",
                           command=self.add_mod)
        m_file.add_separator()
        m_file.add_command(label="保存", accelerator="Ctrl+S", command=self.save)
        m_file.add_command(label="再読み込み", accelerator="F5", command=self.reload)
        m_file.add_separator()
        m_file.add_command(label="ゲームの場所を設定…", command=self.choose_game)
        m_file.add_separator()
        m_file.add_command(label="終了", command=self._on_close)
        bar.add_cascade(label="ファイル", menu=m_file)

        # 開いた瞬間に押せる項目を揃える（postcommand）。
        # 選択が変わるたびに更新すると、
        # 見ていないメニューのために毎回書き換えることになる。
        self.mod_menu = tk.Menu(bar, tearoff=0, postcommand=self._sync_mod_menu)
        self.mod_menu.add_command(label="有効 / 無効", accelerator="Space",
                                  command=self._toggle)
        self.mod_menu.add_command(label="全て有効にする", command=self._enable_all)
        self.mod_menu.add_command(label="設定…", accelerator="Enter",
                                  command=self._edit_settings)
        self.mod_menu.add_separator()
        self.mod_menu.add_command(label="一番上へ", command=lambda: self._move_to(0))
        self.mod_menu.add_command(label="上へ", command=lambda: self._move(-1))
        self.mod_menu.add_command(label="下へ", command=lambda: self._move(1))
        self.mod_menu.add_command(label="一番下へ", command=lambda: self._move_to(-1))
        self.mod_menu.add_separator()
        self.mod_menu.add_command(label="この MOD のフォルダを開く",
                                  command=self.open_mod_dir)
        self.mod_menu.add_command(label="mods/ フォルダを開く",
                                  command=self.open_mods_dir)
        bar.add_cascade(label="MOD", menu=self.mod_menu)

        m_run = tk.Menu(bar, tearoff=0)
        m_run.add_command(label="MOD を注入してゲームを起動", accelerator="F9",
                          command=self.launch)
        m_run.add_command(label="MOD を外す", command=self.unload)
        m_run.add_separator()
        # 失敗したときの案内文が out/bootstrap.log を指すのに、
        # そこへ行く手段がどこにも無かった。
        m_run.add_command(label="out/ フォルダを開く（ログ。消してよい）",
                          command=self.open_out_dir)
        # 「out/ を消してください」と案内できるのは、
        # 消えては困るものが別に置いてあると示せるときだけ。
        # 開く手段もここに並べておく。
        m_run.add_command(label="state/ フォルダを開く（MOD の記録。消すと巻き戻る）",
                          command=self.open_state_dir)
        m_run.add_separator()
        # 絞り込み（`FILTERS`）ではなくメニューに置く。
        # あちらは「表示だけ」の条件で揃えてあり、
        # 注入する中身まで変わる項目を混ぜると意味が濁る。
        m_run.add_checkbutton(label="デバッグモード（開発者向けの MOD を使う）",
                              variable=self.debug_var,
                              command=self._toggle_debug_mode)
        # 切ると注入をまたいでログが積み上がる。
        # 複数回のプレイを追う検証で要る。
        m_run.add_checkbutton(label="注入のたびにログを新しくする",
                              variable=self.log_rotate_var,
                              command=self._toggle_log_rotate)
        bar.add_cascade(label="実行", menu=m_run)

        master.configure(menu=bar)

        for sequence, name in self.MENU_KEYS:
            master.bind(sequence, lambda _e, n=name: (getattr(self, n)(), "break")[1])

        # 一覧を選んだ状態で並べ替えるための割り当て。
        # Treeview 自身は Up/Down を「選択の移動」に使うので、
        # Ctrl を足して住み分ける。
        for sequence, command in (
                ("<Control-Up>", lambda: self._move(-1)),
                ("<Control-Down>", lambda: self._move(1)),
                ("<Control-Home>", lambda: self._move_to(0)),
                ("<Control-End>", lambda: self._move_to(-1))):
            master.bind(sequence, lambda _e, c=command: (c(), "break")[1])

    def _build_row_menu(self) -> None:
        """行の上での右クリック。その mod に対してできることだけを出す。

        一覧が出来てから呼ぶ（menu の親に tree を渡すので、
        `_build_menu` と一緒には作れない）。
        """
        self.row_menu = tk.Menu(self.tree, tearoff=0)
        self.row_menu.add_command(label="有効 / 無効", command=self._toggle)
        self.row_menu.add_command(label="設定…", command=self._edit_settings)
        self.row_menu.add_separator()
        self.row_menu.add_command(label="一番上へ", command=lambda: self._move_to(0))
        self.row_menu.add_command(label="一番下へ", command=lambda: self._move_to(-1))
        self.row_menu.add_separator()
        self.row_menu.add_command(label="フォルダを開く", command=self.open_mod_dir)

    def _sync_mod_menu(self) -> None:
        """メニューバーの「MOD」を開く直前に、押せる項目だけを押せる状態にする。"""
        mod = self._selected()
        picked = "normal" if mod else "disabled"
        for label in ("有効 / 無効", "一番上へ", "上へ", "下へ", "一番下へ",
                      "この MOD のフォルダを開く"):
            self.mod_menu.entryconfigure(label, state=picked)
        self.mod_menu.entryconfigure(
            "設定…",
            state="normal" if (mod and mod["settings"]) else "disabled")
        self.mod_menu.entryconfigure(
            "全て有効にする", state="normal" if self._off_known() else "disabled")

    def _on_context(self, event: tk.Event) -> None:
        row = self.tree.identify_row(event.y)
        if not row:
            return
        # 右クリックでは選択が動かないので、
        # 押した行を選んでから出す（見えている選択と、
        # メニューが効く相手を一致させる）。
        self.tree.selection_set(row)
        self.tree.focus(row)
        mod = self._selected()
        self.row_menu.entryconfigure(
            "設定…",
            state="normal" if (mod and mod["settings"]) else "disabled")
        try:
            self.row_menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.row_menu.grab_release()

    def _update_actions(self) -> None:
        """未保存かどうかを「保存」の押せる/押せないに反映する。

        押せない＝書き戻すものが無い。
        状態表示の行にも「（未保存）」は出るが、そちらは操作のたびに書き換わるので、
        常に見える印をボタン側にも置く。
        """
        self.save_btn.state(["!disabled"] if self.dirty else ["disabled"])

    # -- 一覧 --------------------------------------------------------------
    def reload(self) -> None:
        found = read_mods()
        self.mods = found["mods"]
        self.disabled = found["disabled"]
        self.problems = found["problems"]
        # 一覧を作り直すたびに読み直す。
        # GUI を開いたまま `settings/loader.json` を手で書き換えた場合にも、
        # F5 で追いつけるようにしておく。
        self.debug_mode = found["debug_mode"]
        self.debug_var.set(self.debug_mode)
        # 世代管理は logrotate に聞く。
        # **GUI で覚えない**のが要点で、環境変数や logrotate.py の既定値でも変わるため、
        # こちらで持つと実際と食い違う。
        self.log_rotate_var.set(logrotate.enabled())
        self.settings = C.load_store(RUNTIME_DIR)
        self.status = read_status()
        self.dirty = False
        self._refresh()
        self._update_actions()

        known, off = len(self._known_mods()), len(self._off_known())
        msg = "{} 個の MOD（有効 {} / 無効 {}）".format(known, known - off, off)
        results = self.status.get("mods") or {}
        if results:
            bad = sorted(k for k, v in results.items() if v != "ok")
            ok = len(results) - len(bad)
            msg += " ｜ 前回の注入: {}/{} 適用".format(ok, len(results))
            if bad:
                msg += "（失敗 {}）".format(len(bad))
        # 「待たない」と決めた保留（ローダの `settle_deferred`）。
        # 件数だけ出す。
        # 黙って消すと、クラウド実行のときに `102_` などが何も当てていない理由が一覧から読めなくなる ― 失敗ではないので
        # ⚠ には出さない。
        skipped = (self.status.get("patches") or {}).get("skipped") or []
        if skipped:
            msg += " ｜ この実行では通らない経路のフック {} 件".format(len(skipped))
        self._set_status(msg)
        self._show_warnings()

    def _show_warnings(self) -> None:
        lines = list(self.problems)
        patches = self.status.get("patches") or {}
        unresolved = patches.get("unresolved") or []
        deferred = patches.get("deferred") or []
        if unresolved:
            # ゲームの起動直後に注入すると、まだ import されていないモジュールが多く、
            # 対象が「無い」ように見える。
            # ローダはそれを覚えていて、import された時点で掛け直す（段階適用）。
            # **その途中で status.json を読むと、
            # 後で解決する分まで unresolved に並ぶ**
            # ― 実際に「81 件」と出た直後の再適用で 0 件になった例がある。
            # ここでゲームの更新を疑わせると、正常な起動を毎回誤診することになる。
            # まだ待っている対象があるうちは「途中」と言い、
            # 本当に消えた可能性の話は待ちが無くなってからにする。
            if deferred:
                lines.append(
                    "注入は段階適用の途中です（未 import のモジュール待ち {} 件、"
                    "対象が見つからないフック {} 件）。"
                    "揃うまで数十秒かかります ― 一覧を開き直すと確定した数が出ます"
                    .format(len(deferred), len(unresolved)))
            else:
                lines.append("前回の注入で対象が見つからなかったフック {} 件"
                             "（ゲームが更新された可能性があります）".format(len(unresolved)))
        self.warn.configure(text="\n".join("⚠ " + line for line in lines))

    def _refresh(self, keep: str | None = None) -> None:
        self.tree.delete(*self.tree.get_children())
        results = self.status.get("mods") or {}
        n = 0
        shown = 0
        for mod in self.mods:
            name = mod["dir"]
            on = name not in self.disabled
            # 適用順の番号は**有効なものだけ**に振る。
            # 無効な行に番号があると、
            # 「何番目に読まれるか」が実際とずれて読めてしまう。
            # 伏せている mod も読み込まれないので、同じ理由で番号を食わせない。
            if on and not self._hidden(mod):
                n += 1
            # 番号を数えてから絞る。
            # 絞り込み中に振り直すと、見えている行の番号が実際の適用順とずれる ― 番号が飛ぶ方が、
            # 嘘の番号より読める。
            if not self._matches(mod):
                continue
            shown += 1
            result = results.get(name, "")
            tags = []
            if not on:
                tags.append("off")
            elif result and result != "ok":
                tags.append("bad")
            elif mod.get("superseded"):
                tags.append("superseded")
            # 背景は文字色と独立に付ける。
            # デバッグモードが切なら `_matches` が既に落としているので、
            # ここで改めてモードを見る必要は無い。
            # 1行に付く背景は1つ。
            # 2つ付けても片方しか出ず、どちらが勝つかは Tk の tag の並び次第で
            # 読んで分かる形にならないので、ここで順に振り分ける。
            # 並びは「その行が並んでいる理由」の強い順 ―
            # 取込済は降ろした事実が最優先、次に開発中、計測が最後。
            if mod.get("local"):
                tags.append("local")
            elif mod.get("superseded"):
                tags.append("taken")
            elif mod["wip"]:
                tags.append("wip")
            elif mod["debug"]:
                tags.append("dev")
            # 設定を持つ mod だけ印を出す。
            # 持たない mod で「設定…」を押しても何も無いことが、
            # 一覧の時点で分かるように。
            changed = bool(self.settings.get(name))
            cfg = ("●" if changed else "○") if (mod["settings"] or mod.get("tool")) else ""
            # 色だけでは「なぜ並んでいるのか」が伝わらないので、
            # 名前の後ろに取り込まれた版を出す。
            # 行そのものに書くのは、
            # 選ばないと分からない状態にしないため（一覧を眺めるだけで仕分けられる）。
            # **`mod["name_ja"]` は書き換えない** ― 絞り込みの対象は元の名前のまま。
            label_ja = mod["name_ja"]
            if mod.get("superseded"):
                label_ja += "　〔{} で本体が取込〕".format(mod["superseded"])
            self.tree.insert("", "end", iid=name, tags=tuple(tags),
                             image=self.check_on if on else self.check_off,
                             values=(n if on else "-", mod["kind"] or "-",
                                     label_ja, mod["dir"], cfg,
                                     mod["version"] or "-", mod["author"] or "-",
                                     RESULT_TEXT.get(result, result or "-")))
        self.shown_count = shown
        if self._filtering():
            self.count_label.configure(
                text="{} / {} 件".format(shown, len(self._known_mods())))
        else:
            self.count_label.configure(text="")
        if keep and self.tree.exists(keep):
            self.tree.selection_set(keep)
            self.tree.see(keep)
        self._show_detail()

    @staticmethod
    def _set_entry(entry: ttk.Entry, text: str) -> None:
        """読み取り専用の欄に文字を入れる。書ける状態に戻してから入れ直す。"""
        entry.configure(state="normal")
        entry.delete(0, "end")
        entry.insert(0, text)
        entry.configure(state="readonly")

    def _set_desc(self, blocks) -> None:
        """説明の枠を書き替える。`blocks` は (見た目の種類, 文) の並び。"""
        self.desc.configure(state="normal")
        self.desc.delete("1.0", "end")
        for tag, text in blocks:
            self.desc.insert("end", text + "\n", tag)
        self.desc.configure(state="disabled")
        self.desc.yview_moveto(0.0)      # 選び直したら頭から読む

    def _show_detail(self) -> None:
        mod = self._selected()
        if not mod:
            self.info_name.configure(text="MOD を選んでください")
            self.info_state.configure(text="一覧の行を選ぶと、ここに出ます")
            self._set_entry(self.info_dir, "")
            for value in self.info_meta.values():
                value.configure(text="")
            for widget in ([self.info_toggle, self.info_open, self.info_cfg]
                           + self.info_move):
                widget.state(["disabled"])
            self._set_desc([])
            return

        for widget in [self.info_toggle, self.info_open] + self.info_move:
            widget.state(["!disabled"])
        # 設定を持たない mod で押せてしまうと、「設定がありません」を読むために
        # 押すことになる。持っているかどうかは一覧の「設定」列と同じ判断。
        # 同梱の設定画面（"tool"）を持つ mod も「設定がある」側。
        self.info_cfg.state(["!disabled"] if (mod["settings"] or mod.get("tool"))
                            else ["disabled"])

        state = "無効" if mod["dir"] in self.disabled else "有効"
        self.info_name.configure(text=mod["name_ja"] or mod["dir"])
        self.info_state.configure(text="{} ／ {} ／ entry: {}".format(
            mod["kind"] or "-", state, mod["entry"]))
        self._set_entry(self.info_dir, mod["dir"])
        self.info_meta["author"].configure(text=mod["author"] or "-")
        self.info_meta["version"].configure(
            text="{}　（API {}）".format(mod["version"] or "-", mod["api"]))

        blocks = []
        # 伏せられている理由は行の色だけでは伝わらないので、選んだら言葉で出す。
        # 「戻すかどうか」を判断する材料なので、取り込まれた版まで書く。
        if mod.get("superseded"):
            blocks.append(("note",
                           "ゲーム本体が {} で同じ修正を取り込んだため降ろして"
                           "います（デバッグモードのときだけ読み込まれます）。"
                           "その版より古いゲームで遊ぶなら戻してください。"
                           .format(mod["superseded"])))
        elif mod["debug"]:
            blocks.append(("note", "開発者向けの計測 MOD です"
                                   "（デバッグモードのときだけ読み込まれます）。"))
        elif mod["wip"]:
            blocks.append(("note", "開発中の MOD です（デバッグモードのときだけ"
                                   "読み込まれます。配布物には入りません。"
                                   "TECH.md §2.6）。"))

        blocks.append(("body", mod["desc_ja"] or "（説明なし）"))
        if (self.show_en.get() and mod["desc_en"]
                and mod["desc_en"] != mod["desc_ja"]):
            blocks.append(("en", mod["desc_en"]))

        # 適用順の制約は一覧の並びからは読めないので、選んだときに出す。
        for key, word in (("after", "これより後に適用"),
                          ("before", "これより先に適用")):
            if mod[key]:
                blocks.append(("label", word))
                blocks.append(("meta", "、".join(mod[key])))
        chosen = self.settings.get(mod["dir"]) or {}
        if chosen:
            blocks.append(("label", "変更済みの設定"))
            blocks.append(("meta", "、".join(
                "{}={!r}".format(k, v) for k, v in sorted(chosen.items()))))
        # 前回の注入で当てた対象。どこを触る mod なのかは、これが一番確か。
        by_mod = ((self.status.get("patches") or {}).get("by_mod") or {})
        targets = by_mod.get(mod["dir"]) or []
        if targets:
            blocks.append(("label", "前回適用した対象"))
            shown = "、".join(targets[:4])
            if len(targets) > 4:
                shown += " ほか {} 件".format(len(targets) - 4)
            blocks.append(("meta", shown))
        self._set_desc(blocks)

    def _selected(self) -> dict | None:
        sel = self.tree.selection()
        if not sel:
            return None
        return next((m for m in self.mods if m["dir"] == sel[0]), None)

    # -- 並べ替え ----------------------------------------------------------
    def _move(self, delta: int) -> None:
        """選んでいるものを1つ上 / 下へ。

        隣は**一覧に見えている隣**で数える。
        絞り込み中に全体の並びで1つ動かすと、隠れているものと入れ替わって、
        画面の上では何も起きていないように見える。
        絞り込んでいなければ見えている並び＝全体の並びなので、結果は変わらない。
        """
        mod = self._selected()
        if not mod:
            return
        shown = self._visible_mods()
        if mod not in shown:
            return
        k = shown.index(mod) + delta
        if not 0 <= k < len(shown):
            return
        other = shown[k]
        i, j = self.mods.index(mod), self.mods.index(other)
        self.mods[i], self.mods[j] = self.mods[j], self.mods[i]
        self._mark_dirty(mod["dir"])

    def _move_to(self, pos: int) -> None:
        mod = self._selected()
        if not mod:
            return
        self.mods.remove(mod)
        self.mods.insert(len(self.mods) if pos < 0 else pos, mod)
        self._mark_dirty(mod["dir"])

    def _mark_dirty(self, keep: str, what: str = "順序を変更しました") -> None:
        self.dirty = True
        self._refresh(keep=keep)
        self._update_actions()
        self._set_status(f"{what}（未保存）")

    # -- ドラッグでの並べ替え ------------------------------------------------
    # 行を掴んで動かす。
    # Treeview には並べ替えの仕組みが無いので、
    # 「今どの行の上にいるか」を毎回引いて、その位置へ差し込み直す。
    def _on_press(self, event: tk.Event) -> None:
        row = self.tree.identify_row(event.y)
        if not row:
            self.drag = None
            return
        region = self.tree.identify_region(event.x, event.y)
        # チェックの列（#0）は Treeview の呼び方では "tree"。
        # 押したら切り替え。
        if region == "tree":
            self.drag = None
            self.tree.selection_set(row)
            self._toggle()
            return
        if region != "cell":
            self.drag = None
            return
        # 「設定」の列は設定を開く。
        # 列は番号ではなく名前で引く ― 番号を直に書くと、
        # 列を1つ増やしただけで別の列を押したことになる。
        if self._column_key(event.x) == "cfg":
            self.drag = None
            self.tree.selection_set(row)
            self._edit_settings()
            return
        self.drag = row

    def _column_key(self, x: int) -> str:
        """横位置から列のキーを引く。当たらなければ空。"""
        column = self.tree.identify_column(x)
        try:
            index = int(column.lstrip("#"))
        except ValueError:
            return ""
        if not 1 <= index <= len(self.COLUMNS):
            return ""          # #0（チェックの列）は "tree" 側で拾っている
        return self.COLUMNS[index - 1][0]

    def _on_drag(self, event: tk.Event) -> None:
        if not self.drag:
            return
        target = self.tree.identify_row(event.y)
        if not target or target == self.drag:
            return
        mod = next((m for m in self.mods if m["dir"] == self.drag), None)
        if mod is None:
            self.drag = None
            return
        dest = next(i for i, m in enumerate(self.mods) if m["dir"] == target)
        self.mods.remove(mod)
        self.mods.insert(dest, mod)
        self._mark_dirty(mod["dir"])

    def _on_release(self, _event: tk.Event) -> None:
        self.drag = None

    def _on_double(self, event: tk.Event) -> str | None:
        # 見出しのダブルクリックは列幅の自動調整に取られるので、行だけ拾う。
        if not self.tree.identify_row(event.y):
            return None
        # チェックの列は押した時点で切り替わっている。
        # ここで重ねると2度目の押下と合わせて3回切り替わり、
        # どちらに倒れたのか分からなくなる。
        if self.tree.identify_region(event.x, event.y) == "tree":
            return "break"
        self._toggle()
        return "break"

    # -- 有効 / 無効 --------------------------------------------------------
    def _toggle(self) -> None:
        mod = self._selected()
        if not mod:
            return
        name = mod["dir"]
        if name in self.disabled:
            self.disabled.discard(name)
            what = f"{mod['name_ja']} を有効にしました"
        else:
            self.disabled.add(name)
            what = f"{mod['name_ja']} を無効にしました"
        self._mark_dirty(name, what)

    def _enable_all(self) -> None:
        off = self._off_known()
        if not off:
            return
        keep = self.tree.selection()
        self.disabled -= off
        self._mark_dirty(keep[0] if keep else "", "全て有効にしました")

    def _toggle_debug_mode(self) -> None:
        """デバッグモードの入切。`settings/loader.json` に書いて一覧を作り直す。

        書き先が `load_order.json` ではないのは、
        これが**構成ではなく手元の切り替え**だから。
        伏せている MOD は順序ファイルに載ったまま動かないだけで、
        入れ直せば宣言された位置に戻る。

        切り替えても、**動いているゲームには届かない**。
        効くのは次の注入からで、`discover()` を通るのがそこだけなため。
        """
        want = bool(self.debug_var.get())
        # 一覧を作り直すと未保存の並びが消える。
        # `launch()` と同じ聞き方で先に促す。
        if self.dirty and messagebox.askyesno(
                "未保存の変更",
                "順序と有効/無効の変更が未保存です。保存してから切り替えますか？\n"
                "保存しない場合、変更は失われます。"):
            self.save()
        try:
            flags = C.load_flags(RUNTIME_DIR)
            flags["debug"] = want
            C.save_flags(RUNTIME_DIR, flags)
        except Exception as exc:
            # 書けなかったのにチェックだけ入った状態にしない。
            self.debug_var.set(self.debug_mode)
            messagebox.showerror("設定を保存できませんでした",
                                 f"{type(exc).__name__}: {exc}")
            return

        self.reload()           # 状態表示は件数で上書きされるので、案内はこの後
        if self.debug_mode:
            self._set_status("デバッグモードを入れました"
                             "（計測用・取込済・開発中の MOD が一覧に出ます。"
                             "次の注入から効きます）")
        else:
            self._set_status("デバッグモードを切りました"
                             "（計測用・取込済・開発中の MOD は読み込まれません）")

    def _toggle_log_rotate(self) -> None:
        """out/*.log を注入のたびに新しくするかを切り替える。

        書き先はデバッグモードと同じ `settings/loader.json`。
        読むのは `tools/logrotate.py` で、優先順位はコマンドライン → 環境変数 → このファイル →
        logrotate.py の既定値。

        切ると注入をまたいでログが積み上がる。
        **複数回のプレイを突き合わせる検証**（前の版で出ていた印が出なくなったか、
        など）では、入れ替えられると比較の土台ごと消えるので、
        そのあいだは切っておく。

        `launch()` と違って未保存の並びには触らない。
        ログの設定は MOD の構成とは無関係で、
        ここで保存を促すと関係の無い操作を巻き込む。
        """
        want = bool(self.log_rotate_var.get())
        try:
            flags = C.load_flags(RUNTIME_DIR)
            flags[logrotate.SETTINGS_FLAG_KEY] = want
            C.save_flags(RUNTIME_DIR, flags)
        except Exception as exc:
            # 書けなかったのにチェックだけ動いた状態にしない。
            self.log_rotate_var.set(logrotate.enabled())
            messagebox.showerror("設定を保存できませんでした",
                                 f"{type(exc).__name__}: {exc}")
            return

        # 環境変数の方が強いので、書けても効かないことがある。
        # 黙って食い違うと「切ったのに入れ替わる」と見えるため、その場で断っておく。
        actual = logrotate.enabled()
        self.log_rotate_var.set(actual)
        if actual != want:
            self._set_status(
                "{} に保存しましたが、環境変数 {} が優先されるため今は{}のままです"
                .format(C.FLAGS_NAME, logrotate.ENV_VAR,
                        "ON" if actual else "OFF"))
        elif actual:
            self._set_status("注入のたびにログを新しくします"
                             "（前回ぶんは .1 に残ります）")
        else:
            self._set_status("ログを入れ替えず追記し続けます"
                             "（注入をまたいで比較したいときの設定です）")

    def save(self) -> None:
        try:
            # 伏せている mod も**そのままの位置で**書く。
            # 一覧に出ていないだけで入っていることに変わりはないので、
            # 保存のたびに記述が消えるのは困る。
            write_order([m["dir"] for m in self.mods], self.disabled)
        except Exception as exc:
            messagebox.showerror("保存に失敗しました", f"{type(exc).__name__}: {exc}")
            return
        self.dirty = False
        self._update_actions()
        known, off = len(self._known_mods()), len(self._off_known())
        self._set_status("load_order.json に保存しました"
                         f"（有効 {known - off} / 無効 {off}）")

    # -- MOD 同梱の道具 ----------------------------------------------------
    def _open_tool(self) -> None:
        """`mod.json` の "tool" を別プロセスで開く（`323_` §4 の契約）。

        MOD のコードをこのプロセスに import しない、という原則を守るため
        サブプロセスにする。場所は引数ではなく環境変数で渡す
        （引数だと道具側の書式を縛る）。
        """
        mod = self._selected()
        if not mod or not mod.get("tool"):
            return
        tool = mod["tool"]
        # 在り処は一覧の行が持っている（`local/` の mod も同じ形で開ける）。
        mod_dir = mod.get("path") or os.path.join(MODS_DIR, mod["dir"])
        entry = os.path.join(mod_dir, tool["entry"])
        if not os.path.isfile(entry):
            messagebox.showerror("道具が見つかりません",
                                 "{} が無い。\n{}".format(tool["entry"], mod_dir))
            return
        env = dict(os.environ)
        env["IML_ROOT"] = ROOT
        env["IML_STATE_DIR"] = STATE_DIR
        env["IML_GAME_DIR"] = os.path.dirname(read_config().get("game_path", "") or "")
        env["IML_MOD_SETTINGS"] = C.store_path(RUNTIME_DIR)
        try:
            subprocess.Popen([sys.executable, entry], cwd=mod_dir, env=env)
        except Exception as exc:
            messagebox.showerror("開けませんでした", f"{type(exc).__name__}: {exc}")
            return
        self._set_status("{} の設定画面を開きました（{}）".format(
            mod["name_ja"], tool["label"]["ja"]))

    # -- 設定 --------------------------------------------------------------
    def _edit_settings(self) -> None:
        mod = self._selected()
        if not mod:
            return
        # 同梱の設定画面を持つ mod は、そちらを開く。
        # 宣言の設定（"settings"）もその画面が引き受ける約束
        # （`323_` §4。設定の入口が2つあると、どちらに何があるか覚えることになる）。
        if mod.get("tool"):
            self._open_tool()
            return
        if not mod["settings"]:
            messagebox.showinfo(
                "設定がありません",
                "{} は GUI から変更できる設定を宣言していません。\n"
                "コード側の定数を直接編集してください。".format(mod["name_ja"]))
            return
        chosen = dict(self.settings.get(mod["dir"]) or {})
        dialog = SettingsDialog(self.winfo_toplevel(), mod, chosen)
        if dialog.result is None:
            return          # キャンセル

        store = C.load_store(RUNTIME_DIR)
        store[mod["dir"]] = dialog.result
        try:
            C.save_store(RUNTIME_DIR, store)
        except Exception as exc:
            messagebox.showerror("保存に失敗しました", f"{type(exc).__name__}: {exc}")
            return
        self.settings = C.load_store(RUNTIME_DIR)
        self._refresh(keep=mod["dir"])
        if dialog.result:
            self._set_status("{} の設定を保存しました（{} 件変更・次回の注入から反映）".format(
                mod["name_ja"], len(dialog.result)))
        else:
            self._set_status("{} の設定を既定値に戻しました".format(mod["name_ja"]))

    # -- mod の追加とフォルダ ------------------------------------------------
    def add_mod(self) -> None:
        path = filedialog.askopenfilename(
            title="MOD の zip ファイルを選択（フォルダから追加する場合はキャンセル）",
            filetypes=[("zip", "*.zip"), ("すべて", "*.*")])
        try:
            if path:
                added = install_from_zip(path)
            else:
                folder = filedialog.askdirectory(title="MOD のフォルダを選択")
                if not folder:
                    return
                added = [install_from_folder(folder)]
        except Exception as exc:
            messagebox.showerror("追加に失敗しました", f"{type(exc).__name__}: {exc}")
            return

        self.reload()
        # 置いただけで動く（順序ファイルに無い mod は末尾に回る）が、
        # 順序は保存しておかないと宣言されないままになる。
        self.save()
        self._set_status("追加しました: {}（一覧の末尾・次回の注入から反映）".format(
            ", ".join(added)))
        if added and self.tree.exists(added[0]):
            self.tree.selection_set(added[0])
            self.tree.see(added[0])

    def open_mod_dir(self) -> None:
        """選んでいる mod のフォルダをエクスプローラで開く。

        設定欄に載らない値（辞書やタプル）を直に編むときと、
        同梱データや `mod.json` を覗くときの入口。
        一覧で選んでいるものと同じ場所が開く。
        """
        mod = self._selected()
        if not mod:
            self._set_status("一覧から MOD を選択してください")
            return
        self._open(mod.get("path") or os.path.join(MODS_DIR, mod["dir"]))

    def open_mods_dir(self) -> None:
        self._open(MODS_DIR)

    def open_out_dir(self) -> None:
        """ログと status.json の置き場。注入に失敗したときの案内先。"""
        os.makedirs(OUT_DIR, exist_ok=True)
        self._open(OUT_DIR)

    def open_state_dir(self) -> None:
        """MOD が持つ永続データの置き場。**ログとは別**（消すと遊びが巻き戻る）。"""
        os.makedirs(STATE_DIR, exist_ok=True)
        self._open(STATE_DIR)

    def _open(self, path: str) -> None:
        try:
            os.startfile(path)              # type: ignore[attr-defined]
        except Exception as exc:
            messagebox.showerror("開けませんでした", f"{type(exc).__name__}: {exc}")

    # -- 起動と注入 --------------------------------------------------------
    def choose_game(self) -> str:
        """ゲームの exe を選ばせて覚える。Epic の URL でも構わない。"""
        path = filedialog.askopenfilename(
            title="instantale.exe を選択",
            filetypes=[("実行ファイル", "*.exe"), ("すべて", "*.*")])
        if path:
            cfg = read_config()
            cfg["game_path"] = path
            write_config(cfg)
            self._set_status(f"ゲームの場所を設定しました: {path}")
        return path

    def launch(self) -> None:
        if self.busy:
            return
        if self.dirty and messagebox.askyesno(
                "未保存の変更",
                "順序と有効/無効の変更が未保存です。保存してから起動しますか？\n"
                "保存しない場合、保存済みの内容が注入されます。"):
            self.save()

        running = injector.find_processes(injector.TARGET_EXE)
        game_path = ""
        if not running:
            game_path = read_config().get("game_path", "")
            if not game_path or not os.path.isfile(game_path):
                messagebox.showinfo(
                    "ゲームの場所が未設定",
                    "起動するゲームの場所が未設定です。\n"
                    "instantale.exe を選択してください。")
                game_path = self.choose_game()
                if not game_path:
                    return

        self._start(self._launch_worker, game_path)

    def unload(self) -> None:
        """当てたパッチを剥がす。ゲームは終了しない。

        「完全に元通り」ではないことを押す前に断る。
        剥がせるのは差し替えた関数だけで、
        既に起きた副作用やセーブに書かれた値は戻らない。
        """
        if self.busy:
            return
        procs = injector.find_processes(injector.TARGET_EXE)
        if not procs:
            messagebox.showinfo("ゲームが起動していません",
                                "解除する対象がありません"
                                "（MOD はプロセスの終了時に解除されます）。")
            return
        if len(procs) > 1:
            messagebox.showerror("ゲームが複数起動中",
                                 "instantale.exe が複数起動しています。")
            return
        if not messagebox.askyesno(
                "MOD を外す",
                "適用中のパッチを解除して本来の動作に戻します"
                "（ゲームは終了しません）。\n\n"
                "以下は元に戻りません:\n"
                "  ・起動時に一度だけ実行された処理の結果\n"
                "  ・MOD がセーブデータに書き込んだ値（パーティ・依頼など）\n\n"
                "完全に素の状態で確認する場合は、注入せずに起動し直してください。"):
            return
        self._start(self._unload_worker, procs[0][0])

    def _start(self, worker, arg) -> None:
        self.busy = True
        self.launch_btn.state(["disabled"])
        self.unload_btn.state(["disabled"])
        self._show_progress(True)
        threading.Thread(target=worker, args=(arg,), daemon=True).start()

    def _show_progress(self, active: bool) -> None:
        """作業中の帯を出す / しまう。

        注入は段階適用まで含めると実測 80 秒ほどかかる。
        文字が変わるだけだと、進んでいるのか固まっているのか判らない
        ― 動くものを1つ置いておく。
        止まっているときは出さない。
        ずっと居ると、動いていることの意味が薄れる。
        """
        if active:
            if not self.progress.winfo_ismapped():
                # `before` で状態表示より先に場所を取る。
                # 後ろに回すと、
                # 状態の文が長いとき（段階適用の待ち）に押し出されて幅が残らない。
                self.progress.pack(side="right", padx=(10, 0),
                                   before=self.status_label)
            self.progress.start(12)
        else:
            self.progress.stop()
            self.progress.pack_forget()

    def _launch_worker(self, game_path: str) -> None:
        """別スレッド。tkinter には触らず、進捗はキューへ流す。"""
        def report(msg: str) -> None:
            self.events.put(("status", msg))

        try:
            if game_path:
                report("ゲームを起動中…")
                subprocess.Popen([game_path], cwd=os.path.dirname(game_path))

            report("ゲームのプロセスを検索中…")
            pid = None
            for attempt in range(1, FIND_TRIES + 1):
                procs = injector.find_processes(injector.TARGET_EXE)
                if len(procs) > 1:
                    # 複数動いていると取り違える。
                    # injector.py と同じく自動で選ばない。
                    self.events.put(("error", "instantale.exe が複数起動しています。"
                                              "一つだけにしてください"))
                    return
                if procs:
                    pid = procs[0][0]
                    break
                # Epic 経由だと 1 分近く待つことがある。
                # 何回目かを出して、待っているのか見失ったのかが判るようにする。
                report("ゲームのプロセスを検索中…（{}/{} 回）".format(
                    attempt, FIND_TRIES))
                time.sleep(FIND_POLL)
            if pid is None:
                self.events.put(("error", "ゲームのプロセスが見つかりません"))
                return

            report(f"pid {pid}: 準備待ち（Python の初期化とウィンドウの表示）…")
            if not watcher.wait_until_ready(pid):
                self.events.put(("error", f"pid {pid}: 起動に失敗しました"))
                return

            report(f"pid {pid}: 注入中…")
            injector.rotate_logs(None, log=report)
            ok = watcher.inject_pid(pid)
            if ok:
                self.events.put(("done", f"pid {pid} に注入しました"))
            else:
                self.events.put(("error", f"pid {pid}: 注入に失敗しました"
                                          "（out/bootstrap.log を確認してください）"))
        except Exception as exc:
            self.events.put(("error", f"{type(exc).__name__}: {exc}"))

    def _unload_worker(self, pid: int) -> None:
        try:
            self.events.put(("status", f"pid {pid}: パッチを解除中…"))
            payload = injector.make_bootstrap(
                injector.RUNTIME_DIR, injector.OUT_DIR, injector.BOOT_LOG,
                action="unload")
            rc = injector.inject(pid, payload)
            if rc == 0:
                self.events.put(("done", f"pid {pid}: MOD を外しました"))
            else:
                self.events.put(("error", f"pid {pid}: 解除に失敗しました"
                                          f"（PyRun_SimpleString が {rc}）"))
        except Exception as exc:
            self.events.put(("error", f"{type(exc).__name__}: {exc}"))

    def _drain_events(self) -> None:
        try:
            while True:
                kind, msg = self.events.get_nowait()
                if kind == "status":
                    self._set_status(msg)
                elif kind == "done":
                    self._finish(msg, reload=True)
                elif kind == "error":
                    self._finish(msg)
                    messagebox.showerror("エラー", msg)
                elif kind == "update":
                    self.update_btn.configure(text="更新 (v{})".format(msg))
                    self.update_btn.pack(side="right")
                elif kind == "restart":
                    self._restart()
        except queue.Empty:
            pass
        self._tick = self.after(200, self._drain_events)

    def _finish(self, msg: str, reload: bool = False) -> None:
        self.busy = False
        self.launch_btn.state(["!disabled"])
        self.unload_btn.state(["!disabled"])
        if reload:
            # 結果（status.json）はローダが書き出す。
            # 少し待ってから読む ―注入が返った直後はまだ boot の途中のことがある。
            self.after(1500, self.reload)
            # 1.5 秒では足りないことがある。
            # ゲームの起動直後に注入すると、モジュールが出揃って段階適用が終わるまで実測で 80 秒ほどかかり、その間の
            # status.json は「対象が見つからない」が並んだ途中経過になる。
            # 待ちが残っているうちだけ、確定するまで数回読み直す。
            # 帯はここでは止めない。
            # 注入そのものは終わっていても、数字がまだ動いている ― 止めると「終わった」に見えて、
            # 後から数が変わる。
            self.after(6000, self._reload_while_deferred)
        else:
            self._show_progress(False)
        self._set_status(msg)

    # 段階適用が終わるまでの追従。
    # 待ちが無くなるか回数を使い切ったら止める。
    _SETTLE_CHECKS = 12          # 10 秒間隔で最大 2 分
    _SETTLE_INTERVAL = 10000

    def _reload_while_deferred(self, remaining: int | None = None) -> None:
        if remaining is None:
            remaining = self._SETTLE_CHECKS
        self.reload()          # 状態表示は reload が書く。続きはその後ろに足す
        patches = self.status.get("patches") or {}
        waiting = patches.get("deferred") or []
        if waiting and remaining > 0:
            # 残りの件数を出す。
            # 減っていくのが見えれば、止まっていないと判る。
            self._set_status("{} ｜ 段階適用の途中（未 import {} 件・あと最大 {} 回確認）"
                             .format(self._status_text, len(waiting), remaining))
            self.after(self._SETTLE_INTERVAL,
                       lambda: self._reload_while_deferred(remaining - 1))
            return
        self._show_progress(False)

    def _set_status(self, msg: str) -> None:
        # 直前の文言を覚えておく。
        # 段階適用の待ちは reload が書いた内容の後ろに足したいが、
        # widget から読み戻すと足した分がまた足されて伸び続ける。
        self._status_text = msg
        self.status_label.configure(text=msg)

    # -- 窓の大きさと位置 ----------------------------------------------------
    def _on_configure(self, event: tk.Event) -> None:
        # 中の部品が動いたときにも飛んでくるので、窓そのものの分だけ拾う。
        master = self.winfo_toplevel()
        if event.widget is master and master.state() == "normal":
            self.geom = master.geometry()

    # -- 更新 --------------------------------------------------------------
    def _check_update(self) -> None:
        """別スレッド。新しい版が在ればボタンを出す。ネットが無い等は黙る。"""
        try:
            found = newer_release()
        except Exception:
            return
        if found:
            self.update = found
            self.events.put(("update", found[0]))

    def _update(self) -> None:
        if self.busy or not self.update:
            return
        ver, url = self.update
        if not messagebox.askokcancel(
                "更新",
                "v{} をダウンロードしてこのフォルダへ上書きし、\n"
                "このウィンドウを開き直します。\n\n"
                "{} は残ります。\n"
                "新しい版に無くなった MOD のフォルダも消しません。".format(
                    ver, UPDATE_KEEPS)):
            return
        self._start(self._update_worker, url)

    def _update_worker(self, url: str) -> None:
        """別スレッド。full zip を out\\ へ落として展開し、開き直しを頼む。

        動いている .py を上書きしても平気（Windows は .py をロックしない。
        読み込み済みのものはメモリの上で動いている）。
        だから「次回起動時に差し替え」の二段構えは要らない。
        """
        import urllib.request
        path = os.path.join(OUT_DIR, "update.zip")
        try:
            self.events.put(("status", "ダウンロード中…"))
            os.makedirs(OUT_DIR, exist_ok=True)
            urllib.request.urlretrieve(url, path)
            self.events.put(("status", "展開中…"))
            extract_release(path)
            os.remove(path)
        except Exception as e:
            self.events.put(("error", "更新に失敗しました: {}".format(e)))
            return
        self.events.put(("restart", ""))

    def _restart(self) -> None:
        subprocess.Popen([sys.executable, os.path.abspath(__file__)], cwd=ROOT)
        self._on_close()

    def _on_close(self) -> None:
        master = self.winfo_toplevel()
        update_config(window=self.geom,
                      window_maximized=(master.state() == "zoomed"),
                      info_width=self._info_width())
        if self._tick is not None:
            self.after_cancel(self._tick)
            self._tick = None
        master.destroy()


def main() -> int:
    if os.name != "nt":
        print("ERROR: Windows 専用です。", file=sys.stderr)
        return 2

    # 32bit の Python では注入できない（`instantale.exe` は x64。
    # injector.py も同じことを確かめている）。
    # ここで先に止めるのは、**ランチャーが pythonw で起動していて画面が無いから** ―
    # injector が stderr に出す警告は誰にも見えず、
    # 「ボタンを押しても何も起きない」だけになる。
    # ランチャーは既定のインストール先が無ければ `pyw -3` や PATH へ倒れるので、
    # そこで 32bit を拾う可能性がある。
    if sys.maxsize <= 2 ** 32:
        tk.Tk().withdraw()
        messagebox.showerror(
            "64bit の Python が必要です",
            "いま動いている Python は 32bit です。\n"
            "ゲーム（instantale.exe）は 64bit なので、注入できません。\n\n"
            "python.org から 64bit 版の Python をインストールしてください。\n\n"
            "いま動いている Python:\n" + sys.executable)
        return 2

    root = tk.Tk()
    root.title("Instantale ModLoader  (v{})".format(ml.__version__))
    root.minsize(820, 480)
    # 配色と書体は中身を組む前に決める。
    # 後から差し替えると、既に作られた widget が古い色のまま残る。
    setup_theme(root)
    # 大きさと位置は閉じるときに settings/gui.json へ入る（App._on_close）。
    restore_geometry(root, read_config())
    App(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
