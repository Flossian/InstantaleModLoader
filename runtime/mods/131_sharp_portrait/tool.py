# -*- coding: utf-8 -*-
r"""既存の NPC の顔画像を一括で検出し直して切り直す画面。設定もここで引き受ける。

    python runtime/mods/131_sharp_portrait/tool.py           窓を開く
    python runtime/mods/131_sharp_portrait/tool.py --dump    窓を開かず、世界ごとの数を標準出力に出す

TECH.md §3.12 の契約で動く（`322_battle_bgm` の道具と同じ）。
ローダの設定画面（`tools/gui.py`）が `mod.json` の `"tool"` を見てこのファイルを
サブプロセスで起動し、場所は環境変数で渡す。直接起動したときは自分で探す（`locate()`）。

##### 何をするか

MOD 本体は**これから作られる絵**にしか効かない。
既に居る NPC の顔（`face_image.png`）は、ゲームが見つけられずに全身の縮小になったまま。
この画面はそれを手元の全世界から数え、検出をやり直して切り直す。

  * 検出はゲームの再現（anime → haar、1.1/3）を素の絵で試し、外れたら前処理
    （均一化 / CLAHE / ガンマ / ぼかし）で拾い直す。中身は `faces.py`（MOD 本体と共通）
  * 切り出し元は立ち絵（`reduced_color_image.png`）か、背景を抜いた元絵
    （`no_bg_image.png`。立ち絵が荒くても顔だけ高画質にできる）
  * 書く前に `face_image.orig.png` を残す。1体ずつ、または全部を元に戻せる
  * 検出だけ（書かない）で数を見ることもできる

##### 要るもの

検出に `cv2`（OpenCV）が要る。ゲームの同梱は使えない（別の Python なので）。
無ければ画面にその旨と「cv2 を入れる」のボタンを出す。押すとこの画面を動かしている
Python に `pip install "opencv-python-headless<5"` で入れ、入ったら窓を開き直す。
それまでは設定の部分だけ使える。
カスケードはゲームのフォルダのもの（`runtime/models/face_recognition/`）を読む。

##### 触るもの

書くのは `worlds/<世界>/characters/<名前>/face_image.png` とその `.orig.png` だけ。
セーブは読まない（`image_src` は同じファイル名を指したまま）。
MOD 本体（`sharp_portrait.py`）は import しない。共通部品は `faces.py`。
"""

import io
import json
import os
import queue
import shutil
import struct
import sys
import threading
import time

MOD_DIR = os.path.dirname(os.path.abspath(__file__))
MOD_NAME = os.path.basename(MOD_DIR)

if MOD_DIR not in sys.path:
    sys.path.insert(0, MOD_DIR)

import faces  # noqa: E402  自分の隣

#: `mod.json` の "settings" と同じ名前・同じ既定値（`sharp_portrait.py` の定数と同じ）。
SETTING_DEFAULTS = {"SHARP_PORTRAIT": False, "FACE_RETRY": True}

#: セーブと世界の置き場（`%LOCALAPPDATA%\Darmabeko\Instantale`。`323_` と同じ）。
DATA_VENDOR = ("Darmabeko", "Instantale")

#: NPC のフォルダにある絵。
GENERATED = "generated_image.png"     # SD の出力。検出はこれに対して
NO_BG = "no_bg_image.png"             # 背景を抜いた 512x1024
STANDING = "reduced_color_image.png"  # 立ち絵
FACE = "face_image.png"
FACE_BACKUP = "face_image.orig.png"

#: 顔の状態。
FOUND = "顔"            # 顔が切られている
FALLBACK = "全身の縮小"  # 見つからず、全身の縮小が顔の代わり（幅の2倍が高さ）
MISSING = "無し"        # face_image.png が無い

#: 切り出し元の選択。
SOURCE_STANDING = "standing"
SOURCE_NO_BG = "no_bg"

#: プレビューの一辺（px）。Tk の `PhotoImage` は整数倍しか持てない（`323_` の道具と同じ）。
FACE_BOX = 128


# ----------------------------------------------------------------- 場所と設定
def _add_loader_path(root):
    for runtime in (os.path.join(root, "runtime") if root else "",
                    os.path.normpath(os.path.join(MOD_DIR, os.pardir, os.pardir))):
        if runtime and os.path.isdir(os.path.join(runtime, "instantale_modloader")) \
                and runtime not in sys.path:
            sys.path.insert(0, runtime)


def locate():
    """(root, state_dir, game_dir)。環境変数が無ければ自分で探す。"""
    root = os.environ.get("IML_ROOT") or os.path.normpath(
        os.path.join(MOD_DIR, os.pardir, os.pardir, os.pardir))
    state_dir = os.environ.get("IML_STATE_DIR") or os.path.join(root, "state")
    game_dir = os.environ.get("IML_GAME_DIR") or ""
    if not game_dir:
        try:
            with io.open(os.path.join(root, "settings", "gui.json"), encoding="utf-8") as fh:
                game_path = json.load(fh).get("game_path") or ""
            if game_path:
                game_dir = os.path.dirname(game_path)
        except (OSError, ValueError):
            game_dir = ""
    return root, state_dir, game_dir


def data_dir(override=""):
    """`%LOCALAPPDATA%\\Darmabeko\\Instantale`。`IML_INSTANTALE_DATA` があればそちら。"""
    if override:
        return override
    env = os.environ.get("IML_INSTANTALE_DATA")
    if env:
        return env
    local = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return os.path.join(local, *DATA_VENDOR)


def worlds_dir(base=""):
    return os.path.join(base or data_dir(), "worlds")


def load_settings(root):
    """この MOD に効いている設定。読めなければ既定。"""
    values = dict(SETTING_DEFAULTS)
    try:
        _add_loader_path(root)
        from instantale_modloader import config
        chosen = config.load_store(os.path.join(root, "runtime")).get(MOD_NAME) or {}
    except Exception:
        chosen = {}
    for key in values:
        if isinstance(chosen.get(key), bool):
            values[key] = chosen[key]
    return values


def save_settings(root, values):
    """既定と違う値だけを `mod_settings.json` に書く。他の MOD の項は触らない。"""
    try:
        _add_loader_path(root)
        from instantale_modloader import config
        runtime = os.path.join(root, "runtime")
        store = config.load_store(runtime)
        changed = dict((k, v) for k, v in values.items() if v != SETTING_DEFAULTS.get(k))
        if changed:
            store[MOD_NAME] = changed
        else:
            store.pop(MOD_NAME, None)
        config.save_store(runtime, store)
        return True
    except Exception:
        return False


# ----------------------------------------------------------------- 絵の読み書き
def png_size(path):
    """PNG の (幅, 高さ)。ヘッダだけ読む。読めなければ None。"""
    try:
        with io.open(path, "rb") as fh:
            head = fh.read(26)
    except OSError:
        return None
    if head[:8] != b"\x89PNG\r\n\x1a\n" or len(head) < 24:
        return None
    return struct.unpack(">II", head[16:24])


def face_state(size):
    """`face_image.png` の寸法から、顔かどうかを見分ける。"""
    if size is None:
        return MISSING
    width, height = size
    return FALLBACK if width * 2 == height else FOUND


def load_cv2():
    """`cv2` と `numpy`。無ければ (None, None)。"""
    try:
        import importlib
        importlib.invalidate_caches()          # いま pip で入れた直後でも見つかるように
        import cv2
        import numpy as np
        if not hasattr(cv2, "CascadeClassifier"):     # OpenCV 5 の headless には無い
            return None, None
        return cv2, np
    except ImportError:
        return None, None


#: 道具が使う OpenCV。5 系の headless には `CascadeClassifier` が無いので 4 系に留める。
PIP_PACKAGE = "opencv-python-headless<5"


def pip_command(user=False):
    """`cv2` を入れるコマンド。この画面を動かしている Python に入れる。"""
    command = [sys.executable, "-m", "pip", "install", "--disable-pip-version-check", PIP_PACKAGE]
    if user:
        command.append("--user")
    return command


def install_cv2(report):
    """`cv2` を pip で入れる。出力は1行ずつ `report` へ。入ったら真。

    書き込めない場所（管理者権限の要る Python）なら `--user` で入れ直す。
    """
    import subprocess
    for user in (False, True):
        command = pip_command(user)
        report("$ " + " ".join(command[1:]))
        try:
            proc = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                    text=True, encoding="utf-8", errors="replace")
        except OSError as exc:
            report("起動できない: {}".format(exc))
            return False
        for line in proc.stdout:
            report(line.rstrip("\n"))
        code = proc.wait()
        if code == 0:
            return load_cv2()[0] is not None
        report("pip が {} で終わった{}".format(code, "。--user で入れ直す" if not user else ""))
    return False


def read_image(cv2, np, path, flags=None):
    """日本語のパスでも読めるように、バイト列から復号する。"""
    data = np.fromfile(path, dtype=np.uint8)
    return cv2.imdecode(data, cv2.IMREAD_UNCHANGED if flags is None else flags)


def write_png(cv2, path, image):
    ok, blob = cv2.imencode(".png", image)
    if not ok:
        raise IOError("encode failed: {}".format(path))
    blob.tofile(path)


def load_cascades(cv2, game_dir):
    """ゲームのカスケードを読む。`{ファイル名: CascadeClassifier}`。読めないものは入らない。"""
    found = {}
    for name in faces.CASCADES:
        path = os.path.join(game_dir, faces.CASCADE_DIR, name)
        if os.path.isfile(path):
            cascade = cv2.CascadeClassifier(path)
            if not cascade.empty():
                found[name] = cascade
    return found


# ----------------------------------------------------------------- 世界の走査
class Npc(object):
    """NPC 1体のフォルダ。"""

    def __init__(self, world, name, folder):
        self.world = world
        self.name = name
        self.folder = folder
        self.face = png_size(os.path.join(folder, FACE))
        self.state = face_state(self.face)
        self.standing = png_size(os.path.join(folder, STANDING))
        self.has_generated = os.path.isfile(os.path.join(folder, GENERATED))
        self.has_no_bg = os.path.isfile(os.path.join(folder, NO_BG))
        self.backup = os.path.isfile(os.path.join(folder, FACE_BACKUP))

    def path(self, name):
        return os.path.join(self.folder, name)

    def label(self):
        return "{} / {}".format(self.world, self.name)


def scan(base=""):
    """手元の全世界の NPC。`[Npc]`（世界名・名前の順）。"""
    found = []
    top = worlds_dir(base)
    try:
        worlds = sorted(os.listdir(top))
    except OSError:
        return found
    for world in worlds:
        chars = os.path.join(top, world, "characters")
        if not os.path.isdir(chars):
            continue
        for name in sorted(os.listdir(chars)):
            folder = os.path.join(chars, name)
            if os.path.isdir(folder) and os.path.isfile(os.path.join(folder, GENERATED)):
                found.append(Npc(world, name, folder))
    return found


def summarize(npcs):
    """世界ごとの `(体, 顔, 全身の縮小, 無し, 控えあり)`。"""
    rows = {}
    for npc in npcs:
        row = rows.setdefault(npc.world, [0, 0, 0, 0, 0])
        row[0] += 1
        row[{FOUND: 1, FALLBACK: 2, MISSING: 3}[npc.state]] += 1
        row[4] += npc.backup
    return rows


# ----------------------------------------------------------------- 検出と切り直し
def detect_npc(cv2, np, cascades, npc):
    """`generated_image.png` に検出を掛ける。`(前処理, カスケード名, 顔の箱, 絵の寸法)` か None。"""
    image = read_image(cv2, np, npc.path(GENERATED), cv2.IMREAD_COLOR)
    if image is None:
        return None
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    hit = faces.detect(gray, cascades, cv2, np)
    if hit is None:
        return None
    prep, name, box = hit
    return prep, name, box, (gray.shape[1], gray.shape[0])


def crop_face(cv2, np, npc, box, detect_size, source):
    """検出した絵の座標系の箱で、切り出し元の絵から顔を切る。寸法が違えば比で合わせる。"""
    path = npc.path(NO_BG if source == SOURCE_NO_BG else STANDING)
    image = read_image(cv2, np, path)
    if image is None:
        raise IOError("cannot read {}".format(path))
    height, width = image.shape[:2]
    ratio = width / float(detect_size[0])
    left, top, right, bottom = (int(round(v * ratio)) for v in box)
    left, top = max(left, 0), max(top, 0)
    right, bottom = min(right, width), min(bottom, height)
    if right <= left or bottom <= top:
        raise ValueError("empty crop {}".format((left, top, right, bottom)))
    return image[top:bottom, left:right]


def apply_face(cv2, np, cascades, npc, source, dry_run):
    """1体ぶん。`(結果の文, 書いたか)`。書く前に `face_image.orig.png` を残す。"""
    hit = detect_npc(cv2, np, cascades, npc)
    if hit is None:
        return "見つからず", False
    prep, name, box, detect_size = hit
    how = "{}{}".format("素の絵" if prep is None else prep, " + " + faces.short_name(name))
    crop = faces.crop_box(box, detect_size)
    if dry_run:
        return "{} で見えた {}（検出だけ）".format(how, crop), False
    face = crop_face(cv2, np, npc, crop, detect_size, source)
    target = npc.path(FACE)
    backup = npc.path(FACE_BACKUP)
    if os.path.isfile(target) and not os.path.isfile(backup):
        shutil.copy2(target, backup)
    write_png(cv2, target, face)
    npc.face = (face.shape[1], face.shape[0])
    npc.state = face_state(npc.face)
    npc.backup = os.path.isfile(backup)
    return "{} で拾い、{}x{} に切り直した".format(how, face.shape[1], face.shape[0]), True


def restore_face(npc):
    """`face_image.orig.png` を戻す。戻せたら真。"""
    backup = npc.path(FACE_BACKUP)
    if not os.path.isfile(backup):
        return False
    shutil.move(backup, npc.path(FACE))
    npc.face = png_size(npc.path(FACE))
    npc.state = face_state(npc.face)
    npc.backup = False
    return True


def targets_of(npcs, worlds, everyone):
    """対象。世界で絞り、`everyone` が偽なら全身の縮小（と無し）だけ。"""
    picked = [n for n in npcs if not worlds or n.world in worlds]
    if not everyone:
        picked = [n for n in picked if n.state != FOUND]
    return picked


# ----------------------------------------------------------------- 画面
def dump(base=""):
    npcs = scan(base)
    print("worlds: {}".format(worlds_dir(base)))
    for world, (total, found, fallback, missing, backups) in summarize(npcs).items():
        print("  {:24} {:4} 体  顔 {:4}  全身の縮小 {:4}  無し {:3}  控え {:3}".format(
            world, total, found, fallback, missing, backups))
    print("total {} 体、全身の縮小 {} 体".format(len(npcs), sum(1 for n in npcs if n.state == FALLBACK)))


def build_window(root_dir, game_dir, base=""):
    import tkinter as tk
    from tkinter import messagebox, ttk

    root = tk.Tk()
    root.title("立ち絵の高画質化と顔認識精度の向上")
    root.minsize(980, 640)
    root.geometry("1180x760")
    try:
        sys.path.insert(0, os.path.join(root_dir, "tools"))
        import gui as loader_gui
        loader_gui.setup_theme(root)
    except Exception:
        pass

    cv2, np = load_cv2()
    cascades = load_cascades(cv2, game_dir) if cv2 else {}
    npcs = scan(base)
    results = {}                       # Npc.folder -> 結果の文
    jobs = queue.Queue()
    photos = {}                        # 参照を握らないと捨てられる

    outer = ttk.Frame(root, padding=12)
    outer.pack(fill="both", expand=True)
    ttk.Label(outer, text="立ち絵の高画質化と顔認識精度の向上", style="Title.TLabel").pack(anchor="w")
    ttk.Label(outer, style="Sub.TLabel",
              text="上は MOD の設定。下は既に居る NPC の顔を検出し直して切り直す道具").pack(anchor="w", pady=(0, 8))

    # --- 設定
    settings = load_settings(root_dir)
    box = ttk.LabelFrame(outer, text="設定（ゲームの中で効くもの）", padding=8)
    box.pack(fill="x")
    sharp_var = tk.BooleanVar(value=settings["SHARP_PORTRAIT"])
    retry_var = tk.BooleanVar(value=settings["FACE_RETRY"])
    ttk.Checkbutton(box, text="立ち絵を荒くしない（縮小と減色を通さず、顔もそこから切り直す）",
                    variable=sharp_var).pack(anchor="w")
    ttk.Checkbutton(box, text="顔の検出をやり直す（ゲームが見つけられなかった回だけ）",
                    variable=retry_var).pack(anchor="w")
    setting_status = ttk.Label(box, style="Sub.TLabel", text="")
    setting_status.pack(side="right")

    def save_now():
        ok = save_settings(root_dir, {"SHARP_PORTRAIT": sharp_var.get(), "FACE_RETRY": retry_var.get()})
        setting_status.configure(text="保存した。次の注入から効く" if ok else "保存できなかった（settings/mod_settings.json）")

    ttk.Button(box, text="設定を保存", command=save_now).pack(side="right", padx=(0, 8))

    # --- 道具
    tool = ttk.LabelFrame(outer, text="既に居る NPC の顔を切り直す", padding=8)
    tool.pack(fill="both", expand=True, pady=(8, 0))

    if not cv2:
        missing = ttk.Frame(tool)
        missing.pack(fill="x")
        ttk.Label(missing, foreground="#b42318", justify="left",
                  text="cv2（OpenCV）が見つからないので、この道具は使えない。\n"
                       "ゲームの同梱は別の Python なので借りられない。この画面を動かしている Python に入れる:\n"
                       "    {}".format(" ".join(pip_command()))).pack(anchor="w")
        install_row = ttk.Frame(missing)
        install_row.pack(fill="x", pady=(4, 0))
        install_button = ttk.Button(install_row, text="cv2 を入れる（pip。ネットワークを使う）")
        install_button.pack(side="left")
        install_status = ttk.Label(install_row, style="Sub.TLabel", text="")
        install_status.pack(side="left", padx=(12, 0))
        install_log = tk.Text(missing, height=6, wrap="none", state="disabled")
        install_log.pack(fill="x", pady=(4, 0))
        install_jobs = queue.Queue()

        def report(line):
            install_jobs.put(line)

        def install():
            install_button.state(["disabled"])
            install_status.configure(text="入れている…")

            def work():
                ok = install_cv2(report)
                install_jobs.put(("done", ok))

            threading.Thread(target=work, daemon=True).start()
            poll_install()

        def poll_install():
            finished = None
            while True:
                try:
                    job = install_jobs.get_nowait()
                except queue.Empty:
                    break
                if isinstance(job, tuple):
                    finished = job[1]
                    break
                install_log.configure(state="normal")
                install_log.insert("end", job + "\n")
                install_log.see("end")
                install_log.configure(state="disabled")
            if finished is None:
                root.after(100, poll_install)
            elif finished:
                install_status.configure(text="入った。画面を開き直す")
                root.restart = True
                root.after(600, root.destroy)
            else:
                install_button.state(["!disabled"])
                install_status.configure(text="入らなかった。上の出力を見る")

        install_button.configure(command=install)
    elif not cascades:
        ttk.Label(tool, foreground="#b42318", justify="left",
                  text="ゲームのカスケードが見つからない: {}\n"
                       "ローダの設定画面でゲームの場所を指定してから開き直す".format(
                           os.path.join(game_dir or "<ゲームのフォルダ>", faces.CASCADE_DIR))).pack(anchor="w")

    top = ttk.Frame(tool)
    top.pack(fill="x")
    # 世界の一覧
    ttk.Label(top, text="世界（選ばなければ全部）").grid(row=0, column=0, sticky="w")
    world_list = tk.Listbox(top, selectmode="extended", height=6, exportselection=False)
    world_list.grid(row=1, column=0, rowspan=4, sticky="nsw", padx=(0, 12))
    summary = summarize(npcs)
    world_names = list(summary)
    for world in world_names:
        total, found, fallback, missing, backups = summary[world]
        world_list.insert("end", "{}  ({} 体、全身の縮小 {}{})".format(
            world, total, fallback, "、控え {}".format(backups) if backups else ""))

    everyone_var = tk.BooleanVar(value=False)
    source_var = tk.StringVar(value=SOURCE_STANDING)
    dry_var = tk.BooleanVar(value=False)
    ttk.Label(top, text="対象").grid(row=0, column=1, sticky="w")
    ttk.Radiobutton(top, text="顔が見つかっていない個体だけ（全身の縮小）", variable=everyone_var, value=False).grid(row=1, column=1, sticky="w")
    ttk.Radiobutton(top, text="全員やり直す", variable=everyone_var, value=True).grid(row=2, column=1, sticky="w")
    ttk.Label(top, text="切り出し元").grid(row=0, column=2, sticky="w", padx=(16, 0))
    ttk.Radiobutton(top, text="立ち絵（reduced_color_image.png。いまの見え方に合わせる）",
                    variable=source_var, value=SOURCE_STANDING).grid(row=1, column=2, sticky="w", padx=(16, 0))
    ttk.Radiobutton(top, text="背景を抜いた元絵（no_bg_image.png。立ち絵が荒くても顔は高画質に）",
                    variable=source_var, value=SOURCE_NO_BG).grid(row=2, column=2, sticky="w", padx=(16, 0))
    ttk.Checkbutton(top, text="検出だけ（書かない）", variable=dry_var).grid(row=3, column=1, sticky="w")

    buttons = ttk.Frame(top)
    buttons.grid(row=4, column=1, columnspan=2, sticky="w", pady=(6, 0))
    run_button = ttk.Button(buttons, text="検出して切り直す")
    run_button.pack(side="left")
    restore_button = ttk.Button(buttons, text="選んだ個体を元に戻す")
    restore_button.pack(side="left", padx=(8, 0))
    restore_all_button = ttk.Button(buttons, text="控えのある個体を全部元に戻す")
    restore_all_button.pack(side="left", padx=(8, 0))
    progress = ttk.Label(buttons, style="Sub.TLabel", text="")
    progress.pack(side="left", padx=(16, 0))
    if not cv2 or not cascades:
        run_button.state(["disabled"])

    # 結果の一覧とプレビュー
    body = ttk.Frame(tool)
    body.pack(fill="both", expand=True, pady=(8, 0))
    columns = ("world", "name", "before", "result")
    tree = ttk.Treeview(body, columns=columns, show="headings", selectmode="browse")
    for key, text, width in (("world", "世界", 160), ("name", "名前", 200), ("before", "前", 100), ("result", "結果", 420)):
        tree.heading(key, text=text)
        tree.column(key, width=width, anchor="w")
    scroll = ttk.Scrollbar(body, orient="vertical", command=tree.yview)
    tree.configure(yscrollcommand=scroll.set)
    tree.pack(side="left", fill="both", expand=True)
    scroll.pack(side="left", fill="y")

    side = ttk.Frame(body, padding=(12, 0, 0, 0))
    side.pack(side="left", fill="y")
    blank = tk.PhotoImage(width=FACE_BOX, height=FACE_BOX)
    panes = {}
    for key, title in (("before", "前（控え）"), ("after", "いま")):
        ttk.Label(side, text=title, style="Sub.TLabel").pack(anchor="w")
        pane = tk.Label(side, borderwidth=1, relief="solid", compound="center",
                        foreground="#646b76", image=blank, text="")
        pane.pack(pady=(0, 8))
        panes[key] = pane
    note = ttk.Label(side, style="Sub.TLabel", justify="left", wraplength=FACE_BOX + 40, text="")
    note.pack(anchor="w")

    rows = {}                          # item id -> Npc

    def fit(photo):
        width, height = photo.width(), photo.height()
        if not width or not height:
            return photo
        if max(width, height) > FACE_BOX:
            step = (max(width, height) + FACE_BOX - 1) // FACE_BOX
            return photo.subsample(step, step)
        times = min(FACE_BOX // width, FACE_BOX // height)
        return photo.zoom(times, times) if times > 1 else photo

    def show(pane, path):
        photo = None
        if path and os.path.isfile(path):
            try:
                photo = fit(tk.PhotoImage(file=path))
            except Exception:
                photo = None
        photos[pane] = photo
        pane.configure(image=photo if photo is not None else blank,
                       text="" if photo is not None else "無し")

    def on_select(*_a):
        item = tree.focus()
        npc = rows.get(item)
        if npc is None:
            return
        show(panes["before"], npc.path(FACE_BACKUP) if npc.backup else "")
        show(panes["after"], npc.path(FACE))
        note.configure(text="{}\n顔: {}  立ち絵: {}".format(
            npc.label(),
            "{}x{}".format(*npc.face) if npc.face else "無し",
            "{}x{}".format(*npc.standing) if npc.standing else "無し"))

    tree.bind("<<TreeviewSelect>>", on_select)

    def fill(items):
        for item in tree.get_children():
            tree.delete(item)
        rows.clear()
        for npc in items:
            item = tree.insert("", "end", values=(
                npc.world, npc.name,
                "{}x{}".format(*npc.face) if npc.face else "無し",
                results.get(npc.folder, "")))
            rows[item] = npc

    def selected_worlds():
        return [world_names[i] for i in world_list.curselection()]

    def refresh_worlds():
        summary.clear()
        summary.update(summarize(npcs))
        keep = world_list.curselection()
        world_list.delete(0, "end")
        for world in world_names:
            total, found, fallback, missing, backups = summary.get(world, (0, 0, 0, 0, 0))
            world_list.insert("end", "{}  ({} 体、全身の縮小 {}{})".format(
                world, total, fallback, "、控え {}".format(backups) if backups else ""))
        for i in keep:
            world_list.selection_set(i)

    def run():
        picked = targets_of(npcs, selected_worlds(), everyone_var.get())
        if not picked:
            messagebox.showinfo("対象なし", "その条件に当たる NPC が居ない")
            return
        source = source_var.get()
        if source == SOURCE_NO_BG and not all(n.has_no_bg for n in picked):
            messagebox.showwarning("元絵が無い個体がある",
                                   "no_bg_image.png が無い個体は立ち絵から切る")
        dry = dry_var.get()
        run_button.state(["disabled"])
        results.clear()
        fill(picked)

        def work():
            done = 0
            for npc in picked:
                try:
                    src = source if (source == SOURCE_STANDING or npc.has_no_bg) else SOURCE_STANDING
                    text, _wrote = apply_face(cv2, np, cascades, npc, src, dry)
                except Exception as exc:
                    text = "失敗: {}: {}".format(type(exc).__name__, exc)
                done += 1
                jobs.put((npc, text, done, len(picked)))
            jobs.put(None)

        threading.Thread(target=work, daemon=True).start()
        poll()

    def poll():
        finished = False
        while True:
            try:
                job = jobs.get_nowait()
            except queue.Empty:
                break
            if job is None:
                finished = True
                break
            npc, text, done, total = job
            results[npc.folder] = text
            for item, row in rows.items():
                if row is npc:
                    tree.item(item, values=(npc.world, npc.name,
                                            "{}x{}".format(*npc.face) if npc.face else "無し", text))
            progress.configure(text="{} / {}".format(done, total))
        if finished:
            run_button.state(["!disabled"])
            hits = sum(1 for t in results.values() if "拾" in t or "見えた" in t)
            progress.configure(text="終わり。{} 体中 {} 体で顔が見えた".format(len(results), hits))
            refresh_worlds()
            on_select()
        else:
            root.after(100, poll)

    def restore_selected():
        npc = rows.get(tree.focus())
        if npc is None:
            return
        if restore_face(npc):
            results[npc.folder] = "元に戻した"
        else:
            results[npc.folder] = "控えが無い"
        fill(list(rows.values()))
        refresh_worlds()

    def restore_all():
        picked = [n for n in npcs if n.backup and (not selected_worlds() or n.world in selected_worlds())]
        if not picked:
            messagebox.showinfo("控えなし", "元に戻せる個体が居ない")
            return
        if not messagebox.askyesno("元に戻す", "{} 体の顔を控え（face_image.orig.png）に戻す。よいか".format(len(picked))):
            return
        for npc in picked:
            restore_face(npc)
            results[npc.folder] = "元に戻した"
        fill(picked)
        refresh_worlds()
        progress.configure(text="{} 体を元に戻した".format(len(picked)))

    run_button.configure(command=run)
    restore_button.configure(command=restore_selected)
    restore_all_button.configure(command=restore_all)
    fill(targets_of(npcs, [], False))
    progress.configure(text="{} 体中、全身の縮小が {} 体".format(
        len(npcs), sum(1 for n in npcs if n.state == FALLBACK)))
    return root


def main(argv):
    root_dir, _state_dir, game_dir = locate()
    if "--dump" in argv:
        dump()
        return 0
    # cv2 を入れた直後は窓を組み直す（部品の有無で中身が変わるので、開き直すのが確実）。
    while True:
        window = build_window(root_dir, game_dir)
        window.restart = False
        window.mainloop()
        if not window.restart:
            return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
