#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Instantale ModLoader の GUI。

やることは6つ。

  1. `runtime/mods/` の mod を **適用順に**一覧で見せる
  2. 順序と有効/無効を編集して `load_order.json` に書き戻す
  3. **mod ごとの設定**を編集して `mod_settings.json` に書き戻す
  4. ゲームを起動して、準備が整った時点で注入する
  5. 注入の**結果**を出す（何本入ったか、何が失敗したか、どこが重なったか）
  6. mod を追加する / 外す

一覧の中身は `mod.json` から読む。**mod のコードは一切 import しない**
（一覧を作るためだけに他人の mod のトップレベルを走らせない）。並び順の意味は
上が先で、上から順に適用される。

探索と適用順の判定は**ローダ本体の `discover()` を呼ぶ**。以前はここに同じ規則
（`_` で始まるフォルダを除く / `mod.json` を持つものだけ / 未記載は末尾）を
書き写していたので、片方だけ直すと一覧と実際の適用順がずれた。

名前は日本語と英語を別の列に出す。`mod.json` は片方しか持たないことがあるので、
無い側はもう片方で埋める（列が空のまま並ぶより、同じ文字が2つ並ぶ方が読める）。

有効/無効は `load_order.json` の `"disabled"` に入る。フォルダ名を変えて切る
方式（先頭に `_`）を GUI から使うと、`"order"` の中の名前と食い違うため。

注入と起動は watcher.py の処理をそのまま呼ぶ。GUI 側で条件判定を書き直すと
`watch.bat` と挙動がずれるため、待ち方（インタプリタの初期化＋ウィンドウの出現）
は1か所に置いたままにする。

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

# このファイルは tools/ にある。runtime/ と設定は1階層上（配布フォルダの根）。
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
# 書き戻す先はローダに聞く。手元用の `load_order.local.json` が在ればそちら
# （未公開の MOD を手元で動かしている間、配布用のファイルを書き換えないため）。
# ここで `ORDER_NAME` を直に組むと、GUI で保存するたびに手元の MOD が
# 配布用の順序ファイルへ書き戻される。
OUT_DIR = os.path.join(ROOT, "out")
STATUS_PATH = os.path.join(OUT_DIR, ml.STATUS_NAME)

# MOD が持つ永続データ（進行中の道中、依頼の出所、NPC の控え）。out/ とは別。
# 場所はローダに聞く（`ml.state_dir`）― ゲームの中で書く側と GUI で開く側とで
# 同じ場所を組み立てる規則を2箇所に書かないため。
STATE_DIR = ml.state_dir(RUNTIME_DIR)

# 利用者が選んだものは全部 settings/ に集める（mod ごとの設定は
# instantale_modloader.config が同じフォルダへ mod_settings.json を書く）。
# ここに入るのは「このウィンドウの覚えていること」＝ゲームの場所と窓の大きさ。
SETTINGS_DIR = C.settings_dir(RUNTIME_DIR)
CONFIG_PATH = os.path.join(SETTINGS_DIR, "gui.json")

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
# 色は全部ここに置く。以前は widget を作る場所に "#666" のような文字列が直に
# 書いてあって、1色変えるのに散らばった箇所を追う必要があった。名前は「何色か」
# ではなく「何に使うか」で付ける ― 後で暗い配色に振るときに、使う側を書き換え
# ずに済むように。
PALETTE = {
    "bg":         "#f4f5f7",   # 窓の地
    "surface":    "#ffffff",   # 一覧・入力欄。地より一段手前にあるもの
    "raised":     "#eceef2",   # 触れているボタン
    "pressed":    "#e0e3e9",   # 押されているボタン
    "border":     "#d4d7dd",   # 仕切り線・一覧の外枠
    # 押せるもの（ボタン・入力欄）の輪郭。地が #f4f5f7 で中が白だと、その差は
    # 256 段階で 11 しかない ― 面の色では境目が出ないので、線で示すしかない。
    # 仕切り線と同じ濃さでは足りないため、一段濃い色を別に持つ。
    "control_edge": "#b0b8c4",
    "text":       "#1f2328",   # 本文
    "text_sub":   "#57606a",   # 補足（説明・状態・見出し）
    "text_faint": "#8c959f",   # 既定値・無効な行
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
}

# 日本語と英語が同じ列に並ぶので、両方が同じ太さで出る書体を選ぶ。上から順に
# 探して、入っていなければ Tk の既定のまま（環境によっては Yu Gothic UI が
# 無い ― その場合に読めない字が出るより、既定の書体で出る方がまだ良い）。
FONT_CANDIDATES = ("Yu Gothic UI", "Meiryo UI", "Segoe UI")
FONT_SIZE = 10


def setup_theme(root: tk.Tk) -> ttk.Style:
    """配色と書体を決める。窓を作った直後・中身を組む前に1回だけ呼ぶ。

    土台に **clam** を敷く。Windows の既定は `vista` で、これはボタンや入力欄を
    OS に描かせるため `style.configure` の色がほとんど効かない（配色を1か所に
    まとめても反映されないので、まとめる意味が無くなる）。clam は全部 Tk 側で
    描くので指定が通る。

    戻り値の `Style` は使わなくても良いが、後から名前付きの style を足したい
    ときのために返しておく。
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
    # コンボボックスの一覧は ttk ではなく素の Listbox なので、style ではなく
    # option データベース越しにしか色を渡せない。
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

    # -- ラベルの役割。呼ぶ側は色ではなく「何のラベルか」を指定する。
    style.configure("Title.TLabel",
                    font=(family or "", FONT_SIZE + 5, "bold"))
    style.configure("Sub.TLabel", foreground=p["text_sub"])
    style.configure("Faint.TLabel", foreground=p["text_faint"])
    style.configure("Warn.TLabel", foreground=p["warn"])
    # ボタンの群に付ける小さな見出し。区切り線だけだと「なぜここで切れているか」
    # が伝わらないので、群の名前を出す。
    style.configure("Group.TLabel", foreground=p["text_faint"],
                    font=(family or "", FONT_SIZE - 1, "bold"))

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

    # -- 一覧。行の高さは既定だと日本語が窮屈なので広げる。
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

    # -- 入力まわり。触れている欄が分かるように、焦点だけ枠を色で示す。
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
    # チェックボタンの印は clam に描かせない。clam の既定の印は、この大きさだと
    # 「入」が × に見える ― 有効にしているのに否定の記号が出るので、意味が逆に
    # 伝わる。一覧の行と**同じ絵**に差し替えて、塗りつぶしの有無で示す。
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

    # 作業中の帯。溝を地に近い色にして、動く部分だけが目に入るようにする。
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
# 以前は「☑」「☐」の**文字**を一覧に流し込んでいた。この2つは字形が似ていて、
# 小さい字だと ☑ の中のチェックが × に潰れる ― 有効なのに「禁止」に見えるので、
# 意味が逆に伝わる。字ではなく絵にして、入/切を塗りつぶしの有無で分ける。
#
# 描き方は素の tk だけで済ませる（PIL を要求すると、配布物に依存が増える）。
# 背景は透明のままにする。行の色で塗ると、選択された行の上で四角が浮く。


def _stroke(img: tk.PhotoImage, color: str,
            x0: float, y0: float, x1: float, y1: float, weight: int) -> None:
    """2点を結ぶ線を置く。PhotoImage には線を引く手段が無いので点で埋める。

    太さは通る点を**中心**に広げる。左上を起点にすると、太くするほど線全体が
    右下へずれる ― 四角の中でチェックが右下に寄って見えるのはこれが原因だった。

    刻みは長さより細かく取る。点を等間隔に置くだけなので、粗いと斜めの線が
    切れて破線に見える。
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

    角を丸めるのは Windows 11 の見た目に寄せるため。直角のままだと、同じ画面に
    並ぶ OS 側の部品から浮く。
    """
    p = PALETTE
    img = tk.PhotoImage(master=master, width=size, height=size)
    radius = max(2, int(round(size * 0.22)))
    if not on:
        # 枠だけ。外周を枠色で塗ってから、1px 内側を地の色で抜く。
        _rounded(img, p["check_edge"], 0, 0, size, size, radius)
        _rounded(img, p["surface"], 1, 1, size - 1, size - 1, max(1, radius - 1))
        return img
    _rounded(img, p["check"], 0, 0, size, size, radius)
    # チェックは細めに、四角の中で余白を残す形で置く。太くすると中が詰まって、
    # 印ではなく「塗り」に見える ― 入っているかどうかが形から読めなくなる。
    #
    # 16px を基準にした比率。太さは点を中心に広がる（`_stroke`）ので、この3点が
    # 描く形の外接がそのまま四角の中央に来る。左右・上下とも画素 4〜11 に収まり、
    # 中心が四角の中心（0〜15 の真ん中）と一致する。
    k = size / 16.0
    weight = max(2, int(round(size / 8)))
    _stroke(img, p["on_accent"], 4.5 * k, 8.0 * k, 6.8 * k, 10.5 * k, weight)
    _stroke(img, p["on_accent"], 6.8 * k, 10.5 * k, 11.0 * k, 5.0 * k, weight)
    return img


# 作った絵はここで抱えたままにする。PhotoImage は Python 側の参照が切れると
# 中身ごと消える（Tk が握るのは名前だけ）ので、貼った先から絵が消える。
_CHECKS: dict[bool, tk.PhotoImage] = {}


def _image_alive(img: tk.PhotoImage, master: tk.Misc) -> bool:
    """`img` が `master` の Tk にまだ在るか。

    PhotoImage は作られた Tk インタプリタに属する。窓を閉じて作り直すと、
    Python 側の参照は残るのに中身は消えていて、貼ろうとした所で落ちる。
    """
    try:
        master.tk.call("image", "type", str(img))
        return True
    except tk.TclError:
        return False


def check_images(master: tk.Misc) -> tuple[tk.PhotoImage, tk.PhotoImage]:
    """（入, 切）を返す。1度作って使い回す。

    一覧の行と、mod ごとの設定ウィンドウの両方がこれを使う。同じ「入」が場所に
    よって違う形で出ると、どちらかが別の意味に見える。
    """
    if _CHECKS and not _image_alive(_CHECKS[True], master):
        _CHECKS.clear()          # 別の Tk になっている。作り直す
    if not _CHECKS:
        # 画面の拡大率に合わせる。字だけ大きくなって絵が取り残されると目立つ。
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
def read_mods() -> dict:
    """一覧に出すものを揃えて返す。判定はローダの `discover()` に任せる。

    **無効なものも一覧には出す**（消えたように見せない）。適用順の番号は
    有効なものだけに振る。

    開発者向けの MOD（`discover()` の `debug`）も**落とさずに返す**。デバッグモードが
    切のときに描画から外すのは `_matches()` の仕事で、`self.mods` からは消さない ―
    `save()` が `self.mods` の並びをそのまま `order` に書き戻すので、ここで落とすと
    保存した瞬間に `load_order.json` から記述ごと消える。
    """
    found = ml.discover(MODS_DIR)
    disabled = set(found["disabled"])

    mods = []
    for name in found["listed"]:
        manifest = found["manifests"].get(name) or {}
        mods.append({
            "dir": name,
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
            # 本体が取り込んだので降ろした mod。値は取り込まれた版（"main_024"）。
            # 読み込みの扱いは debug と同じだが、表示では分ける（伏せた理由が違う）。
            "superseded": manifest.get("superseded") or "",
        })
    return {"mods": mods, "disabled": disabled, "problems": found["problems"],
            "debug_mode": bool(found.get("debug_mode"))}


def write_order(names: list[str], disabled: set[str]) -> None:
    """順序と無効一覧を書き戻す。

    `disabled` は `names` の並びで書く。差分を見たときに一覧と同じ順で並ぶ方が
    追いやすいのと、無効にした順で溜まっていくのを避けるため。
    """
    off = [n for n in names if n in disabled]
    with open(ml.order_path(MODS_DIR), "w", encoding="utf-8") as fh:
        json.dump({"order": names, "disabled": off}, fh,
                  ensure_ascii=False, indent=2)
        fh.write("\n")


def read_config() -> dict:
    data = _read_json(CONFIG_PATH)
    return data if isinstance(data, dict) else {}


def write_config(data: dict) -> None:
    os.makedirs(SETTINGS_DIR, exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
        fh.write("\n")


def update_config(**values) -> None:
    """`settings/gui.json` の一部だけ書き換える。

    覚えていることが増えても（窓の位置・ゲームの場所…）互いを消さないように、
    必ず読んでから書く。**書けなくても止めない** ― 設定を残せないことと、
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
# 初めて開くときの大きさ。一覧 15 行と、MOD を選んだときに伸びる説明欄
# （最大 5 行）と、足元のボタンが全部入る高さを取る。1000x620 では、選んだ
# 瞬間に説明が伸びて一覧が潰れ、窓の下が詰まっていた。
GEOMETRY_DEFAULT = "1180x760"


def _on_screen(root: tk.Misc, geom: str) -> bool:
    """`WxH+X+Y` が画面の中に居るか。

    覚えた位置をそのまま使うと、モニタを外した後などに**画面の外へ出て掴めない
    窓**になる。左上が仮想デスクトップの内側にあることだけ確かめて、外れていたら
    位置は捨てて大きさだけ使う。
    """
    try:
        size, x, y = geom.replace("+-", "+~").split("+")
        x, y = int(x.replace("~", "-")), int(y.replace("~", "-"))
        w, h = (int(v) for v in size.split("x"))
    except Exception:
        return False
    # 仮想デスクトップ（マルチモニタ全体）の範囲。左と上は負にもなりうる。
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

    これがこのウィンドウと「実際に動いたゲーム」の唯一の接点。ローダが boot の
    最後に書き出す（`instantale_modloader.write_status`）。注入が成功したかどうかと
    **mod が入ったかどうかは別の話**なので、ここを読まないと「28個中3個が失敗」を
    利用者に出せない。
    """
    data = _read_json(STATUS_PATH)
    return data if isinstance(data, dict) else {}


# --------------------------------------------------------------------------
# mod の追加
# --------------------------------------------------------------------------
def install_from_zip(zip_path: str) -> list[str]:
    """zip を `runtime/mods/` へ展開する。入っている mod のフォルダ名を返す。

    `mod.json` を持つフォルダを mod とみなす（ローダの探索規則と同じ）。zip の
    作り方は2通りあるので両方受ける。

        mymod.zip/mod.json              中身だけを固めたもの → zip 名をフォルダ名にする
        mymod.zip/mymod/mod.json        フォルダごと固めたもの → その名前を使う

    展開先を絶対パスで検査してから書く。zip の中の名前は信用できない
    （`../` を含む細工で mods/ の外へ書ける）。
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
                # 中身だけの zip。zip のファイル名をフォルダ名にする。
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

    既定と同じ値は**書かない**。`mod_settings.json` に残すのは利用者が変えたものだけで、
    そうしておくと mod の既定値が新しい版で変わったときにその変更が届く
    （全部書き出すと、既定を上書きし続ける形になって永久に古い値で動く）。

    窓の幅と位置は覚える（`settings/gui.json` の `settings_window`）。**mod ごとでは
    なく1つ**で、どの mod の設定を開いても同じ場所に出る ― 設定を見て回るときに
    毎回同じ所に出る方が追いやすい。高さは覚えない。設定の数で変わるので、
    覚えた高さを当てると項目が切れるか余白が空く。
    """

    def __init__(self, master: tk.Misc, mod: dict, chosen: dict):
        super().__init__(master)
        self.title("{} の設定".format(mod["name_ja"]))
        # Toplevel は素の tk widget なので、ttk の style ではなく直に地の色を渡す
        # （渡さないと本体の窓と違う灰色が出る）。
        self.configure(background=PALETTE["bg"])
        self.transient(master)
        self.resizable(True, False)
        self.mod = mod
        self.result: dict | None = None
        self.vars: dict[str, tuple[dict, tk.Variable]] = {}

        frame = ttk.Frame(self, padding=12)
        frame.pack(fill="both", expand=True)
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
            # 既定値を必ず見せる。「元に戻したい」ときに何に戻すのかが分かるように。
            ttk.Label(frame, text="既定: {!r}".format(decl["default"]),
                      style="Faint.TLabel").grid(row=row, column=1, sticky="w")
            row += 1

        frame.columnconfigure(1, weight=1)
        bar = ttk.Frame(frame)
        bar.grid(row=row, column=0, columnspan=2, sticky="e", pady=(12, 0))
        ttk.Button(bar, text="既定に戻す", command=self._reset).pack(side="left", padx=4)
        ttk.Button(bar, text="キャンセル", command=self._close).pack(side="left", padx=4)
        ttk.Button(bar, text="OK", command=self._ok).pack(side="left")

        self.bind("<Escape>", lambda _e: self._close())
        self.protocol("WM_DELETE_WINDOW", self._close)
        self._restore()
        self.grab_set()
        self.wait_window()

    # -- 窓の幅と位置 --------------------------------------------------------
    def _restore(self) -> None:
        geom = read_config().get("settings_window")
        if not isinstance(geom, str) or "x" not in geom:
            return          # 覚えていなければ Tk の既定の置き場所に任せる
        # 中身を組み終えてからでないと、必要な高さが分からない。
        self.update_idletasks()
        try:
            width = int(geom.split("x")[0])
        except ValueError:
            return
        width = max(width, self.winfo_reqwidth())
        size = "{}x{}".format(width, self.winfo_reqheight())
        if "+" in geom and _on_screen(self, geom):
            pos = geom[geom.index("+"):]
        else:
            # 覚えた位置が画面の外。大きさだけ指定すると画面の左上に張り付くので、
            # 呼び出し元の窓に重ねる（Tk が transient に選ぶのと同じ見え方）。
            master = self.master
            pos = "+{}+{}".format(master.winfo_rootx() + 60,
                                  master.winfo_rooty() + 60)
        self.geometry(size + pos)

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
        # int / float / str。空欄は allow_null の設定で「未指定」を表す。
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
            if isinstance(raw, str) and raw.strip() == "":
                raw = None      # 空欄 = 未指定
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
    何を表しているかも、押せることも、触ってみるまで判らない。列の意味を
    見出しに全部書くと幅が要るので、指した所だけ出す。

    `text` には文字列のほか、event を受けて文字列を返す関数を渡せる。列ごとに
    中身を変えるため ― 出す物が変わったら出し直し、空を返したらしまう。
    """

    DELAY = 550          # ms。動かしている最中に次々出さないための間

    def __init__(self, widget: tk.Misc, text) -> None:
        self.widget = widget
        self.text = text
        self.tip: tk.Toplevel | None = None
        self.pending: str | None = None
        self.showing = ""
        # add="+" で足す。この widget には既に別の用途の割り当てがある
        # （一覧のドラッグなど）ので、置き換えると壊れる。
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
    # (キー, 見出し, 幅, 寄せ, 伸ばすか)
    # 「有効」はここに無い。チェックは絵なので、Treeview で絵を置ける唯一の列
    # （一番左の #0）に入れる。
    COLUMNS = (
        ("order", "順", 40, "center", False),
        ("name_ja", "Name（日本語）", 200, "w", True),
        ("name_en", "Name (English)", 200, "w", True),
        ("cfg", "設定", 44, "center", False),
        ("version", "Ver", 46, "center", False),
        ("author", "Author", 120, "w", False),
        ("state", "前回", 76, "center", False),
    )

    # 列に指したときに出す説明。記号だけの列（有効・設定）は、意味と「押せる」
    # ことの両方を書く ― 見出しの文字数では入りきらない。
    COLUMN_HELP = {
        "on": "この MOD を適用するかどうか。クリックで切り替え（Space キーでも同じ）",
        "order": "上から適用される順番。無効なものには番号を振りません",
        "name_ja": "行をドラッグすると適用順を変えられます",
        "name_en": "行をドラッグすると適用順を変えられます",
        "cfg": "● = 既定から変更あり ／ ○ = 既定のまま。クリックで設定を開きます\n"
               "（印が無い MOD は、変更できる設定を持っていません）",
        "version": "mod.json に書かれた版",
        "author": "mod.json に書かれた作者",
        "state": "前回の注入の結果。「適用」以外だった行は赤で出ます",
    }

    # 絞り込みの種類。並び順は「よく使う順」。
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
        self._status_text = ""            # 状態表示に今出ている文言
        self.shown_count = 0              # 絞り込みの結果、一覧に出ている数
        # デバッグモード。切のあいだ、開発者向けの MOD は一覧に出ないし
        # 読み込まれもしない。実体は settings/loader.json で、ローダも同じ値を読む。
        self.debug_mode = False
        # 絞り込みの条件。widget より先に作る（`_build` の中で参照する）。
        self.query = tk.StringVar()
        self.filter_mode = tk.StringVar(value=self.FILTERS[0])
        # メニューのチェックの見た目。実体は `self.debug_mode` の方で、
        # こちらは表示専用（`reload` が毎回 `set` で揃える）。
        self.debug_var = tk.BooleanVar(value=False)
        # ログの世代管理。こちらは GUI 側に控えを持たず、チェックの状態が
        # そのまま今の値になる（`reload` が logrotate に聞いて揃える）。
        # 実体は debug と同じ settings/loader.json だが、**読むのはローダではなく
        # 注入する側**（tools/logrotate.py）。
        self.log_rotate_var = tk.BooleanVar(value=True)
        # 注入は別スレッドで動くので、進捗はキュー越しに受け取って
        # メインスレッドの after で描く（tkinter は他スレッドから触れない）。
        self.events: queue.Queue[tuple[str, str]] = queue.Queue()
        # 最大化していない状態の大きさと位置。最大化中の値を覚えると、次に
        # 開いたときに画面いっぱいの「普通の窓」になってしまうので分けて持つ。
        self.geom = master.geometry()

        self._build()
        self.reload()
        # 進捗を拾う繰り返し。閉じるときに止める（止めないと、消えた widget を
        # 相手に1回だけ走って Tk がエラーを吐く）。
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

        # 絞り込み。MOD が 30 個を超えると、上から目で追うしか無くなる。
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
        # 条件が変わったら組み直す。入力のたびに走るが、30 個程度なら気にならない。
        self.query.trace_add("write", self._on_filter_change)
        self.filter_mode.trace_add("write", self._on_filter_change)

        # body は作るだけで、置くのは最後。pack は先に置いた物から場所を配り、
        # 足りなくなった分は後ろの物から削られる ― 一覧を先に置くと、窓が低い
        # ときに**足元のボタンごと消える**（MOD を選んで説明が伸びた瞬間に、
        # 起動ボタンが画面の外に出ていたのはこれ）。下に付く物を先に確保して、
        # 余りを一覧に渡す。
        body = ttk.Frame(self)

        # `show` に "tree" を含めるのは、#0 の列にチェックの絵を置くため
        # （"headings" だけだと #0 が隠れて、絵を出す場所が無くなる）。
        self.check_on, self.check_off = check_images(self)
        self.tree = ttk.Treeview(body, columns=[c[0] for c in self.COLUMNS],
                                 show="tree headings", selectmode="browse",
                                 height=15)
        self.tree.heading("#0", text="有効", anchor="center")
        self.tree.column("#0", width=52, minwidth=52, stretch=False,
                         anchor="center")
        for key, title, width, anchor, stretch in self.COLUMNS:
            # 見出しは中身と同じ側に寄せる。ttk の既定は中央なので、左寄せの列
            # （名前・作者）で見出しだけが中に浮いて、列の切れ目が読めなくなる。
            self.tree.heading(key, text=title, anchor=anchor)
            self.tree.column(key, width=width, anchor=anchor, stretch=stretch)
        # 無効な行は灰色にする。チェックだけだと、一覧を眺めたときに
        # 「効いていない mod がある」ことに気付きにくい。
        self.tree.tag_configure("off", foreground=PALETTE["text_faint"])
        # 前回の注入で入らなかった mod は赤。ログを開かずに気付けるように。
        self.tree.tag_configure("bad", foreground=PALETTE["danger"])
        # 本体が取り込んだので降ろした mod。デバッグモードのときだけ並ぶので、
        # **計測 MOD と一緒に見えることになる**。同じ見た目だと「なぜ出ているのか」が
        # 混ざるため、こちらだけ色を変える（無効な行の灰色とも別にする ―
        # 切ってあるのではなく、要らなくなったので降ろした、という違いがある）。
        self.tree.tag_configure("superseded", foreground=PALETTE["text_sub"])
        self.tree.pack(side="left", fill="both", expand=True)
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
        bar.pack(side="left", fill="y")
        self.tree.configure(yscrollcommand=bar.set)

        # 一覧の脇に残すのは**並べ替えだけ**。ここは「一覧の中の位置を動かす」
        # 操作しか無い場所にする ― 以前は追加・フォルダを開く・保存まで同じ幅で
        # 12 個並んでいて、区切り線はあっても群の意味が伝わらず、よく使うものを
        # 毎回探すことになっていた。残りはメニューと右クリックに移した。
        side = ttk.Frame(body, padding=(12, 0, 0, 0))
        side.pack(side="left", fill="y")
        ttk.Label(side, text="並べ替え", style="Group.TLabel").pack(
            anchor="w", pady=(0, 5))
        for label, command in (
                ("▲ 上へ", lambda: self._move(-1)),
                ("▼ 下へ", lambda: self._move(1)),
                ("⤒ 最上部へ", lambda: self._move_to(0)),
                ("⤓ 最下部へ", lambda: self._move_to(-1))):
            ttk.Button(side, text=label, width=12, command=command).pack(
                fill="x", pady=1)
        # ボタンを減らした分、他の操作の在り処を書いておく。減らしただけだと
        # 「できなくなった」ように見える。
        ttk.Label(side, text="そのほかの操作は\n行を右クリック、\nまたは上のメニュー",
                  style="Faint.TLabel", justify="left").pack(
            anchor="w", pady=(14, 0))

        self.detail = ttk.Label(self, text="", style="Sub.TLabel",
                                wraplength=860, justify="left")
        # 宣言と実体のずれ（順序・依存・非互換）。何も無ければ行ごと出さない。
        self.warn = ttk.Label(self, text="", style="Warn.TLabel",
                              wraplength=860, justify="left")

        foot = ttk.Frame(self)
        # このウィンドウで押すものの中で、これだけが「実際にゲームが変わる」操作。
        # 他と同じ濃さで並べると毎回探すことになるので、色を持たせて1つだけ立たせる。
        self.launch_btn = ttk.Button(foot, text="MOD を注入してゲームを起動",
                                     style="Accent.TButton", command=self.launch)
        self.launch_btn.pack(side="left")
        self.unload_btn = ttk.Button(foot, text="MOD を外す", command=self.unload)
        self.unload_btn.pack(side="left", padx=6)
        # 保存は一覧の脇ではなく起動の隣に置く。「並べ替えて、保存して、起動する」
        # が一続きの流れなので、最後の2つが同じ場所にある方が追える。押せるかどうか
        # が未保存の有無をそのまま表す（`_update_actions`）。
        self.save_btn = ttk.Button(foot, text="保存", command=self.save)
        self.save_btn.pack(side="left")
        self.status_label = ttk.Label(foot, text="", style="Sub.TLabel")
        self.status_label.pack(side="left", padx=10)
        # 作業中の帯。動いていないときは出さない。
        #
        # 幅は決め打ちで短くする。clam の動く部分は溝の長さに関わらず 30px 弱に
        # しかならないので、行いっぱいに伸ばすと、長い溝の上を小さな点が滑るだけ
        # になって、動いていることが伝わらない。
        self.progress = ttk.Progressbar(foot, mode="indeterminate", length=180,
                                        style="Thin.Horizontal.TProgressbar")

        # ここで置く順が、窓が足りないときに何を守るかを決める。下から順に
        # 確保して、最後に残りを一覧へ渡す。一覧は縮んでも行が減るだけだが、
        # ボタンは消えると押せなくなる。
        foot.pack(side="bottom", fill="x", pady=(6, 0))
        self.warn.pack(side="bottom", fill="x")
        self.detail.pack(side="bottom", fill="x", pady=(8, 0))
        body.pack(side="top", fill="both", expand=True, pady=(8, 0))

    # -- 絞り込み ------------------------------------------------------------
    def _hidden(self, mod: dict) -> bool:
        """デバッグモードが切のあいだ伏せる mod か。

        絞り込み（`_matches`）とは別物として扱う。絞り込みは利用者が今かけている
        条件で、解除すれば戻る。こちらは**存在しないものとして扱う**もので、
        件数の分母にも入れない。

        伏せる理由は2つあり、扱いは同じ（`discover()` の `hide`）:

          debug       計測用。開発者以外には意味が無い
          superseded  ゲーム本体が同じ修正を取り込んだので降ろした

        どちらもデバッグモードを入れれば出てくる。**表示だけは分ける**ので、
        ここで一緒にするのは「出すか出さないか」の判定だけに留める。
        """
        return (mod["debug"] or bool(mod.get("superseded"))) and not self.debug_mode

    def _known_mods(self) -> list[dict]:
        """利用者から見て「入っている」mod。件数の分母はこちら。"""
        return [m for m in self.mods if not self._hidden(m)]

    def _off_known(self) -> set[str]:
        """切られている mod のうち、一覧に出ているものだけ。

        伏せている mod の有効/無効は**利用者に見えないので触らない**。まとめて
        有効にする操作がここを通ることで、見えないものまで巻き込まずに済む。
        """
        return self.disabled & {m["dir"] for m in self._known_mods()}

    def _matches(self, mod: dict) -> bool:
        """この mod を一覧に出すか。"""
        if self._hidden(mod):
            return False
        text = self.query.get().strip().lower()
        if text:
            # 説明も対象にする。「BGM」で探して、名前に入っていない mod まで
            # 見つかる方が、探している側の意図に近い。
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
        # チェックの列は #0。cell ではなく "tree" として来るが、見出しの上では
        # "heading" になるので、列番号でも見る。
        if region == "tree" or self.tree.identify_column(event.x) == "#0":
            return self.COLUMN_HELP["on"]
        return self.COLUMN_HELP.get(self._column_key(event.x), "")

    # -- メニュー ------------------------------------------------------------
    #  一覧の脇から外した操作の行き先。ここに置くのは「たまにしか押さないが
    #  無いと困る」もの ― 出しっぱなしにすると、よく使うものが埋もれる。
    #  ショートカットも同じ場所で決める（メニューに出る表記と実際の割り当てが
    #  離れていると、片方だけ直したときに嘘になる）。
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

        # 開いた瞬間に押せる項目を揃える（postcommand）。選択が変わるたびに
        # 更新すると、見ていないメニューのために毎回書き換えることになる。
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
        # 失敗したときの案内文が out/bootstrap.log を指すのに、そこへ行く手段が
        # どこにも無かった。
        m_run.add_command(label="out/ フォルダを開く（ログ。消してよい）",
                          command=self.open_out_dir)
        # 「out/ を消してください」と案内できるのは、消えては困るものが別に
        # 置いてあると示せるときだけ。開く手段もここに並べておく。
        m_run.add_command(label="state/ フォルダを開く（MOD の記録。消すと巻き戻る）",
                          command=self.open_state_dir)
        m_run.add_separator()
        # 絞り込み（`FILTERS`）ではなくメニューに置く。あちらは「表示だけ」の
        # 条件で揃えてあり、注入する中身まで変わる項目を混ぜると意味が濁る。
        m_run.add_checkbutton(label="デバッグモード（開発者向けの MOD を使う）",
                              variable=self.debug_var,
                              command=self._toggle_debug_mode)
        # 切ると注入をまたいでログが積み上がる。複数回のプレイを追う検証で要る。
        m_run.add_checkbutton(label="注入のたびにログを新しくする",
                              variable=self.log_rotate_var,
                              command=self._toggle_log_rotate)
        bar.add_cascade(label="実行", menu=m_run)

        master.configure(menu=bar)

        for sequence, name in self.MENU_KEYS:
            master.bind(sequence, lambda _e, n=name: (getattr(self, n)(), "break")[1])

        # 一覧を選んだ状態で並べ替えるための割り当て。Treeview 自身は Up/Down を
        # 「選択の移動」に使うので、Ctrl を足して住み分ける。
        for sequence, command in (
                ("<Control-Up>", lambda: self._move(-1)),
                ("<Control-Down>", lambda: self._move(1)),
                ("<Control-Home>", lambda: self._move_to(0)),
                ("<Control-End>", lambda: self._move_to(-1))):
            master.bind(sequence, lambda _e, c=command: (c(), "break")[1])

    def _build_row_menu(self) -> None:
        """行の上での右クリック。その mod に対してできることだけを出す。

        一覧が出来てから呼ぶ（menu の親に tree を渡すので、`_build_menu` と
        一緒には作れない）。
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
        # 右クリックでは選択が動かないので、押した行を選んでから出す
        # （見えている選択と、メニューが効く相手を一致させる）。
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

        押せない＝書き戻すものが無い。状態表示の行にも「（未保存）」は出るが、
        そちらは操作のたびに書き換わるので、常に見える印をボタン側にも置く。
        """
        self.save_btn.state(["!disabled"] if self.dirty else ["disabled"])

    # -- 一覧 --------------------------------------------------------------
    def reload(self) -> None:
        found = read_mods()
        self.mods = found["mods"]
        self.disabled = found["disabled"]
        self.problems = found["problems"]
        # 一覧を作り直すたびに読み直す。GUI を開いたまま `settings/loader.json` を
        # 手で書き換えた場合にも、F5 で追いつけるようにしておく。
        self.debug_mode = found["debug_mode"]
        self.debug_var.set(self.debug_mode)
        # 世代管理は logrotate に聞く。**GUI で覚えない**のが要点で、環境変数や
        # logrotate.py の既定値でも変わるため、こちらで持つと実際と食い違う。
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
        self._set_status(msg)
        self._show_warnings()

    def _show_warnings(self) -> None:
        lines = list(self.problems)
        patches = self.status.get("patches") or {}
        unresolved = patches.get("unresolved") or []
        deferred = patches.get("deferred") or []
        if unresolved:
            # ゲームの起動直後に注入すると、まだ import されていないモジュールが多く、
            # 対象が「無い」ように見える。ローダはそれを覚えていて、import された時点で
            # 掛け直す（段階適用）。**その途中で status.json を読むと、後で解決する分まで
            # unresolved に並ぶ** ― 実際に「81 件」と出た直後の再適用で 0 件になった例がある。
            #
            # ここでゲームの更新を疑わせると、正常な起動を毎回誤診することになる。
            # まだ待っている対象があるうちは「途中」と言い、本当に消えた可能性の話は
            # 待ちが無くなってからにする。
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
            # 適用順の番号は**有効なものだけ**に振る。無効な行に番号があると、
            # 「何番目に読まれるか」が実際とずれて読めてしまう。伏せている mod も
            # 読み込まれないので、同じ理由で番号を食わせない。
            if on and not self._hidden(mod):
                n += 1
            # 番号を数えてから絞る。絞り込み中に振り直すと、見えている行の番号が
            # 実際の適用順とずれる ― 番号が飛ぶ方が、嘘の番号より読める。
            if not self._matches(mod):
                continue
            shown += 1
            result = results.get(name, "")
            tags = ()
            if not on:
                tags = ("off",)
            elif result and result != "ok":
                tags = ("bad",)
            elif mod.get("superseded"):
                tags = ("superseded",)
            # 設定を持つ mod だけ印を出す。持たない mod で「設定…」を押しても
            # 何も無いことが、一覧の時点で分かるように。
            changed = bool(self.settings.get(name))
            cfg = ("●" if changed else "○") if mod["settings"] else ""
            # 色だけでは「なぜ並んでいるのか」が伝わらないので、名前の後ろに
            # 取り込まれた版を出す。行そのものに書くのは、選ばないと分からない
            # 状態にしないため（一覧を眺めるだけで仕分けられる）。
            # **`mod["name_ja"]` は書き換えない** ― 絞り込みの対象は元の名前のまま。
            label_ja = mod["name_ja"]
            if mod.get("superseded"):
                label_ja += "　〔{} で本体が取込〕".format(mod["superseded"])
            self.tree.insert("", "end", iid=name, tags=tags,
                             image=self.check_on if on else self.check_off,
                             values=(n if on else "-",
                                     label_ja, mod["name_en"], cfg,
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

    def _show_detail(self) -> None:
        mod = self._selected()
        if not mod:
            self.detail.configure(text="")
            return
        # 押せる/押せないの出し分けはメニューを開くときに行う（`_sync_mod_menu`）。
        state = "無効" if mod["dir"] in self.disabled else "有効"
        bits = ["{}  /  entry: {}  /  API {}  /  {}".format(
            mod["dir"], mod["entry"], mod["api"], state)]
        # 伏せられている理由は行の色だけでは伝わらないので、選んだら言葉で出す。
        # 「戻すかどうか」を判断する材料なので、取り込まれた版まで書く。
        if mod.get("superseded"):
            bits.append("ゲーム本体が {} で同じ修正を取り込んだため降ろしています"
                        "（デバッグモードのときだけ読み込まれます）。"
                        "その版より古いゲームで遊ぶなら戻してください。"
                        .format(mod["superseded"]))
        elif mod["debug"]:
            bits.append("開発者向けの計測 MOD です"
                        "（デバッグモードのときだけ読み込まれます）。")
        desc = mod["desc_ja"] or "（説明なし）"
        if mod["desc_en"] and mod["desc_en"] != mod["desc_ja"]:
            desc += "\n" + mod["desc_en"]
        bits.append(desc)

        # 適用順の制約は一覧の並びからは読めないので、選んだときに出す。
        for key, word in (("after", "これより後に適用"), ("before", "これより先に適用")):
            if mod[key]:
                bits.append("{}: {}".format(word, ", ".join(mod[key])))
        chosen = self.settings.get(mod["dir"]) or {}
        if chosen:
            bits.append("変更済みの設定: " + ", ".join(
                "{}={!r}".format(k, v) for k, v in sorted(chosen.items())))

        # 前回の注入で当てた対象。どこを触る mod なのかは、これが一番確か。
        by_mod = ((self.status.get("patches") or {}).get("by_mod") or {})
        targets = by_mod.get(mod["dir"]) or []
        if targets:
            shown = ", ".join(targets[:4])
            if len(targets) > 4:
                shown += " ほか {} 件".format(len(targets) - 4)
            bits.append("前回適用した対象: " + shown)
        self.detail.configure(text="\n".join(bits))

    def _selected(self) -> dict | None:
        sel = self.tree.selection()
        if not sel:
            return None
        return next((m for m in self.mods if m["dir"] == sel[0]), None)

    # -- 並べ替え ----------------------------------------------------------
    def _move(self, delta: int) -> None:
        """選んでいるものを1つ上 / 下へ。

        隣は**一覧に見えている隣**で数える。絞り込み中に全体の並びで1つ動かすと、
        隠れているものと入れ替わって、画面の上では何も起きていないように見える。
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
    #  行を掴んで動かす。Treeview には並べ替えの仕組みが無いので、
    #  「今どの行の上にいるか」を毎回引いて、その位置へ差し込み直す。
    def _on_press(self, event: tk.Event) -> None:
        row = self.tree.identify_row(event.y)
        if not row:
            self.drag = None
            return
        region = self.tree.identify_region(event.x, event.y)
        # チェックの列（#0）は Treeview の呼び方では "tree"。押したら切り替え。
        if region == "tree":
            self.drag = None
            self.tree.selection_set(row)
            self._toggle()
            return
        if region != "cell":
            self.drag = None
            return
        # 「設定」の列は設定を開く。列は番号ではなく名前で引く ― 番号を直に書くと、
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
        # チェックの列は押した時点で切り替わっている。ここで重ねると2度目の押下と
        # 合わせて3回切り替わり、どちらに倒れたのか分からなくなる。
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

        書き先が `load_order.json` ではないのは、これが**構成ではなく利用者の
        切り替え**だから。伏せている MOD は順序ファイルに載ったまま動かないだけで、
        入れ直せば宣言された位置に戻る。

        切り替えても、**動いているゲームには届かない**。効くのは次の注入からで、
        `discover()` を通るのがそこだけなため。
        """
        want = bool(self.debug_var.get())
        # 一覧を作り直すと未保存の並びが消える。`launch()` と同じ聞き方で先に促す。
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
                             "（計測用の MOD と、本体が取り込んだので降ろした MOD が"
                             "一覧に出ます。次の注入から効きます）")
        else:
            self._set_status("デバッグモードを切りました"
                             "（計測用と取込済みの MOD は読み込まれません）")

    def _toggle_log_rotate(self) -> None:
        """out/*.log を注入のたびに新しくするかを切り替える。

        書き先はデバッグモードと同じ `settings/loader.json`。読むのは
        `tools/logrotate.py` で、優先順位は
        コマンドライン → 環境変数 → このファイル → logrotate.py の既定値。

        切ると注入をまたいでログが積み上がる。**複数回のプレイを突き合わせる検証**
        （前の版で出ていた印が出なくなったか、など）では、入れ替えられると比較の
        土台ごと消えるので、そのあいだは切っておく。

        `launch()` と違って未保存の並びには触らない。ログの設定は MOD の構成とは
        無関係で、ここで保存を促すと関係の無い操作を巻き込む。
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

        # 環境変数の方が強いので、書けても効かないことがある。黙って食い違うと
        # 「切ったのに入れ替わる」と見えるため、その場で断っておく。
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
            # 伏せている mod も**そのままの位置で**書く。一覧に出ていないだけで
            # 入っていることに変わりはないので、保存のたびに記述が消えるのは困る。
            write_order([m["dir"] for m in self.mods], self.disabled)
        except Exception as exc:
            messagebox.showerror("保存に失敗しました", f"{type(exc).__name__}: {exc}")
            return
        self.dirty = False
        self._update_actions()
        known, off = len(self._known_mods()), len(self._off_known())
        self._set_status("load_order.json に保存しました"
                         f"（有効 {known - off} / 無効 {off}）")

    # -- 設定 --------------------------------------------------------------
    def _edit_settings(self) -> None:
        mod = self._selected()
        if not mod:
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
        # 置いただけで動く（順序ファイルに無い mod は末尾に回る）が、順序は
        # 保存しておかないと宣言されないままになる。
        self.save()
        self._set_status("追加しました: {}（一覧の末尾・次回の注入から反映）".format(
            ", ".join(added)))
        if added and self.tree.exists(added[0]):
            self.tree.selection_set(added[0])
            self.tree.see(added[0])

    def open_mod_dir(self) -> None:
        """選んでいる mod のフォルダをエクスプローラで開く。

        設定欄に載らない値（辞書やタプル）を直に編むときと、同梱データや
        `mod.json` を覗くときの入口。一覧で選んでいるものと同じ場所が開く。
        """
        mod = self._selected()
        if not mod:
            self._set_status("一覧から MOD を選択してください")
            return
        self._open(os.path.join(MODS_DIR, mod["dir"]))

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

        「完全に元通り」ではないことを押す前に断る。剥がせるのは差し替えた関数だけで、
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

        注入は段階適用まで含めると実測 80 秒ほどかかる。文字が変わるだけだと、
        進んでいるのか固まっているのか判らない ― 動くものを1つ置いておく。
        止まっているときは出さない。ずっと居ると、動いていることの意味が薄れる。
        """
        if active:
            if not self.progress.winfo_ismapped():
                # `before` で状態表示より先に場所を取る。後ろに回すと、状態の
                # 文が長いとき（段階適用の待ち）に押し出されて幅が残らない。
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
                    # 複数動いていると取り違える。injector.py と同じく自動で選ばない。
                    self.events.put(("error", "instantale.exe が複数起動しています。"
                                              "一つだけにしてください"))
                    return
                if procs:
                    pid = procs[0][0]
                    break
                # Epic 経由だと 1 分近く待つことがある。何回目かを出して、
                # 待っているのか見失ったのかが判るようにする。
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
        except queue.Empty:
            pass
        self._tick = self.after(200, self._drain_events)

    def _finish(self, msg: str, reload: bool = False) -> None:
        self.busy = False
        self.launch_btn.state(["!disabled"])
        self.unload_btn.state(["!disabled"])
        if reload:
            # 結果（status.json）はローダが書き出す。少し待ってから読む
            # ―注入が返った直後はまだ boot の途中のことがある。
            self.after(1500, self.reload)
            # 1.5 秒では足りないことがある。ゲームの起動直後に注入すると、
            # モジュールが出揃って段階適用が終わるまで実測で 80 秒ほどかかり、
            # その間の status.json は「対象が見つからない」が並んだ途中経過になる。
            # 待ちが残っているうちだけ、確定するまで数回読み直す。
            #
            # 帯はここでは止めない。注入そのものは終わっていても、数字がまだ
            # 動いている ― 止めると「終わった」に見えて、後から数が変わる。
            self.after(6000, self._reload_while_deferred)
        else:
            self._show_progress(False)
        self._set_status(msg)

    # 段階適用が終わるまでの追従。待ちが無くなるか回数を使い切ったら止める。
    _SETTLE_CHECKS = 12          # 10 秒間隔で最大 2 分
    _SETTLE_INTERVAL = 10000

    def _reload_while_deferred(self, remaining: int | None = None) -> None:
        if remaining is None:
            remaining = self._SETTLE_CHECKS
        self.reload()          # 状態表示は reload が書く。続きはその後ろに足す
        patches = self.status.get("patches") or {}
        waiting = patches.get("deferred") or []
        if waiting and remaining > 0:
            # 残りの件数を出す。減っていくのが見えれば、止まっていないと判る。
            self._set_status("{} ｜ 段階適用の途中（未 import {} 件・あと最大 {} 回確認）"
                             .format(self._status_text, len(waiting), remaining))
            self.after(self._SETTLE_INTERVAL,
                       lambda: self._reload_while_deferred(remaining - 1))
            return
        self._show_progress(False)

    def _set_status(self, msg: str) -> None:
        # 直前の文言を覚えておく。段階適用の待ちは reload が書いた内容の後ろに
        # 足したいが、widget から読み戻すと足した分がまた足されて伸び続ける。
        self._status_text = msg
        self.status_label.configure(text=msg)

    # -- 窓の大きさと位置 ----------------------------------------------------
    def _on_configure(self, event: tk.Event) -> None:
        # 中の部品が動いたときにも飛んでくるので、窓そのものの分だけ拾う。
        master = self.winfo_toplevel()
        if event.widget is master and master.state() == "normal":
            self.geom = master.geometry()

    def _on_close(self) -> None:
        master = self.winfo_toplevel()
        update_config(window=self.geom,
                      window_maximized=(master.state() == "zoomed"))
        if self._tick is not None:
            self.after_cancel(self._tick)
            self._tick = None
        master.destroy()


def main() -> int:
    if os.name != "nt":
        print("ERROR: Windows 専用です。", file=sys.stderr)
        return 2

    # 32bit の Python では注入できない（`instantale.exe` は x64。injector.py も
    # 同じことを確かめている）。ここで先に止めるのは、**ランチャーが pythonw で
    # 起動していて画面が無いから** ― injector が stderr に出す警告は誰にも
    # 見えず、「ボタンを押しても何も起きない」だけになる。
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
    root.title("Instantale ModLoader")
    root.minsize(820, 480)
    # 配色と書体は中身を組む前に決める。後から差し替えると、既に作られた
    # widget が古い色のまま残る。
    setup_theme(root)
    # 大きさと位置は閉じるときに settings/gui.json へ入る（App._on_close）。
    restore_geometry(root, read_config())
    App(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
