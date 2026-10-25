# -*- coding: utf-8 -*-
r"""攻撃の演出の設定画面。描き足した絵の見え方（控えめ・はっきり）を選び、両方の見本を動かして見比べる。

    python runtime/mods/342_attack_animations/tool.py

TECH.md §3.12 の契約で動く。ローダの設定画面（`tools/gui.py`）が `mod.json` の `"tool"` を見て
このファイルを別プロセスで開き、場所は環境変数で渡す。直接起動したときは自分で探す。

なぜ独自の画面か: 見え方の違いは文では伝わらないので、絵を並べて動かしたいため。
一括設定だけ（MOD 本体がモジュールのグローバルしか読まない。`338_` と同じ）。
項目の名前・型・既定値・選択肢は `mod.json` の `"settings"` が唯一の出所（`modtool.decls`）。
絵は `effects\<見え方>\<演出>\frame_*.png`（MOD 本体が戦闘で出すのと同じ物）を読む。
"""
import os
import sys

MOD_DIR = os.path.dirname(os.path.abspath(__file__))

# 共有の土台（`tools/modtool.py`）を import できるようにする（`338_` の tool.py と同じ探し方）。
_IML_ROOT = os.environ.get("IML_ROOT") or ""
for _tools in ([os.path.join(_IML_ROOT, "tools")] if _IML_ROOT else []) + [
        os.path.normpath(os.path.join(MOD_DIR, os.pardir, os.pardir, os.pardir, "tools"))]:
    if os.path.isfile(os.path.join(_tools, "modtool.py")) and _tools not in sys.path:
        sys.path.insert(0, _tools)

import modtool  # noqa: E402

#: 見え方（設定 `ART_STYLE` の値）と、`effects\` の下のフォルダ。並びは画面の列の並び。
STYLES = (("soft", "控えめ"), ("bright", "はっきり"))
#: 見本に出す演出と表示名（DOC.md の表の言い方）。並びは画面の行の並び。
EFFECTS = (
    ("heavy_slash", "重い一撃"), ("cross_slash", "十字斬り"), ("thrust", "突き"),
    ("energy_slash", "光の刃"), ("whip", "鞭"), ("claw", "爪"), ("bite", "噛みつき"),
    ("arrow_shot", "矢"), ("thrown_weapon", "投擲"),
    ("gun_shot", "銃撃"), ("rapid_fire", "連射"), ("shotgun_blast", "散弾"), ("sniper_shot", "狙撃"),
    ("energy_beam", "光線"), ("shockwave", "衝撃波"), ("poison", "毒"), ("poison_aoe", "毒（全体）"),
)
#: 1コマの時間（ミリ秒）と、1回流した後の間（ミリ秒）。ゲームの絵の再生は約 0.07 秒ずつ（VERIFICATION.md §3.111）。
FRAME_MS = 70
PAUSE_MS = 500
#: 見本の大きさ（画面の画素）。ゲームは1体の演出を 192 角で出すが、見本は17種を1画面に収めるので絵の等倍の 64 角にする。
#: 全体の演出（元絵 128 角）も同じ升に縮める（大きい升があると列ごと広がる）。
SHOWN_SIZE = 64
#: 1行に並べる演出の数（1つの演出が控えめ・はっきりの2升）。17種が6行に収まり、スクロールしない。
PER_ROW = 3
#: 止めている間に出すコマの位置（コマの数に対する割合）。頭のコマはほぼ空で最後のコマは空なので、絵がいちばん出ている中ほどを出す。
STILL_AT = 0.45
#: 見本の背景（表示名, 色）。戦闘の画面は暗いので黒が既定。
BACKGROUNDS = (("黒", "#000000"), ("暗い灰色", "#2a2a2e"), ("明るい灰色", "#f0f0f2"))


def frames_of(mod_dir, style, effect):
    """その見え方・演出のコマの PNG の並び（名前順）。無ければ空。"""
    folder = os.path.join(mod_dir, "effects", style, effect)
    if not os.path.isdir(folder):
        return []
    return [os.path.join(folder, name) for name in sorted(os.listdir(folder))
            if name.lower().endswith(".png")]


def sample_rows(mod_dir=MOD_DIR):
    """見本の行。`[(演出, 表示名, {見え方: [コマ]})]`。どの見え方にもコマが無い演出は外す。"""
    rows = []
    for effect, label in EFFECTS:
        frames = dict((style, frames_of(mod_dir, style, effect)) for style, _name in STYLES)
        if any(frames.values()):
            rows.append((effect, label, frames))
    return rows


def still_index(count):
    """止めている間に出すコマの番号（`STILL_AT`）。コマが無ければ 0。"""
    return min(max(0, count - 1), int(count * STILL_AT)) if count > 0 else 0


def scale_for(width):
    """`SHOWN_SIZE` の升に収める倍率 `(拡大, 縮小)`。どちらも整数（画素を潰さない）で、片方は 1。"""
    width = max(1, int(width))
    if width > SHOWN_SIZE:
        return 1, -(-width // SHOWN_SIZE)
    return max(1, SHOWN_SIZE // width), 1


def shown_width(width):
    """升に出したときの幅（`scale_for` を掛けた後）。"""
    zoom, sub = scale_for(width)
    return max(1, int(width)) * zoom // sub


def build_window(mod_dir=MOD_DIR):
    """設定画面を組んで返す（`mainloop()` は呼ばない。検査が中を数えるため）。"""
    import time
    import tkinter as tk
    from tkinter import messagebox, ttk

    info = modtool.manifest(mod_dir)
    title = (info.get("name") or {}).get("ja") or modtool.mod_name(mod_dir)
    root_dir, _state_dir, _game_dir = modtool.locate(mod_dir)
    runtime = os.path.join(root_dir, "runtime")
    config = modtool.config_module(root_dir, mod_dir)
    found = modtool.decls(mod_dir, root_dir)
    current = modtool.load_settings(root_dir, mod_dir)

    root = tk.Tk()
    root.title(title)
    root.minsize(820, 740)           # 見本の6行が切れない高さ
    modtool.restore_window(root_dir, mod_dir, root, "900x820")
    modtool.setup_theme(root, root_dir)

    outer = ttk.Frame(root, padding=12)
    outer.pack(fill="both", expand=True)
    ttk.Label(outer, text=title, style="Title.TLabel").pack(anchor="w")
    ttk.Label(outer, style="Sub.TLabel", wraplength=780, justify="left",
              text="設定はどの世界にも効く（settings\\mod_settings.json）。ゲームを起動し直すと効く。\n"
                   "見本は、描き足した絵をゲームの戦闘と同じ速さで繰り返し動かす。\n"
                   "ゲームに入っていた絵（打撃・爆発・風・光・闇・魔力など）は見え方を選べないので出さない。"
              ).pack(anchor="w", pady=(0, 8))

    footer = ttk.Frame(outer)
    footer.pack(side="bottom", fill="x")
    ttk.Separator(outer).pack(side="bottom", fill="x", pady=8)

    holder = ttk.Frame(outer)
    holder.pack(fill="x")
    form = modtool._Form(holder, dict((key, found[key]) for key in found),
                         lambda k: "既定: " + modtool.shown(found[k]["default"], found[k]))

    picker = ttk.Frame(outer)
    picker.pack(fill="x", pady=(12, 4))
    ttk.Label(picker, text="見本", style="Group.TLabel").pack(side="left")
    ttk.Label(picker, text="背景", style="Group.TLabel").pack(side="left", padx=(16, 8))
    bg_box = ttk.Combobox(picker, state="readonly", width=12, values=[name for name, _c in BACKGROUNDS])
    bg_box.current(0)
    bg_box.pack(side="left")

    body = ttk.Frame(outer)            # 17種が収まる大きさなので、スクロールの部品は使わない
    body.pack(anchor="nw", pady=(4, 0))
    cells = []          # 背景を塗り直す升（tk.Label）
    heads = {}          # 見え方 → 列の見出し（いまの設定の列に印を付ける）
    players = []        # [{"label", "frames": [PhotoImage], "at"}]

    def background():
        return BACKGROUNDS[max(0, bg_box.current())][1]

    width = 1 + len(STYLES)            # 1つの演出が使う列（名前 + 見え方ごと）
    for block in range(PER_ROW):
        for offset, (style, name) in enumerate(STYLES, start=1):
            head = ttk.Label(body, text=name, style="Group.TLabel")
            head.grid(row=0, column=block * width + offset, padx=6, pady=(0, 4))
            heads.setdefault(style, []).append(head)

    rows = sample_rows(mod_dir)
    for index, (effect, label, frames) in enumerate(rows):
        row, block = 1 + index // PER_ROW, index % PER_ROW
        ttk.Label(body, text=label).grid(row=row, column=block * width, sticky="e",
                                         padx=(12 if block else 0, 6))
        for offset, (style, _name) in enumerate(STYLES, start=1):
            column = block * width + offset
            paths = frames.get(style) or []
            images = []
            for path in paths:
                try:
                    image = tk.PhotoImage(file=path)
                except tk.TclError:
                    continue
                zoom, sub = scale_for(image.width())
                images.append(image.zoom(zoom) if zoom > 1 else image.subsample(sub) if sub > 1 else image)
            still = still_index(len(images))
            cell = tk.Label(body, image=images[still] if images else "", text="" if images else "（絵が無い）",
                            bg=background(), bd=0, padx=3, pady=3)
            cell.grid(row=row, column=column, padx=4, pady=3)
            cells.append(cell)
            if images:
                players.append({"label": cell, "frames": images, "at": still, "still": still})

    def on_background(_event=None):
        for cell in cells:
            cell.configure(bg=background())

    bg_box.bind("<<ComboboxSelected>>", on_background)

    def mark_current(*_args):
        chosen = form.get().get("ART_STYLE")
        for style, name in STYLES:
            for head in heads.get(style, []):
                head.configure(text=("▶ " + name) if style == chosen else name)

    for var in form.vars.values():
        var.trace_add("write", mark_current)

    pause_ticks = max(1, PAUSE_MS // FRAME_MS)
    for player in players:
        player["step"] = -pause_ticks    # 負の間は止め絵

    def tick():
        """見本を1コマずつ進める。見本ごとに「1回流す → 止め絵（`STILL_AT` のコマ）で `PAUSE_MS` 止める」を繰り返す。

        揃えて待たせると、短い見本は長い見本（全体の毒）が終わるまで空のコマのままになるので、見本ごとに回す。
        同じ演出の控えめとはっきりはコマの数が同じなので、左右は揃って動く。
        """
        if not root.winfo_exists():
            return
        for player in players:
            player["step"] += 1
            if player["step"] >= len(player["frames"]):
                player["step"] = -pause_ticks
            at = player["step"] if player["step"] >= 0 else player["still"]
            if at != player["at"]:
                player["at"] = at
                player["label"].configure(image=player["frames"][at])
        root.after(FRAME_MS, tick)

    if players:
        root.after(FRAME_MS, tick)

    # ---- 値の出し入れ（`338_` の tool.py と同じ）
    def as_shown(values):
        return dict((k, v if isinstance(v, bool) else str(v)) for k, v in values.items())

    saved = {"values": {}}
    last = {"values": current}

    def load_values(values):
        form.set(dict((k, values.get(k, found[k]["default"])) for k in form.vars))
        saved["values"] = as_shown(dict((k, values.get(k, found[k]["default"])) for k in found))
        mark_current()

    load_values(current)

    def dirty():
        return as_shown(dict((k, v) for k, v in form.get().items() if k in found)) != saved["values"]

    status = ttk.Label(footer, style="Faint.TLabel", text="ゲームを起動し直すと効きます")
    status.pack(side="left")

    def save():
        values, bad = modtool.coerce_all(mod_dir, form.get(), root_dir)
        if bad:
            messagebox.showerror("設定を確かめてください", bad, parent=root)
            return False
        try:
            modtool.save_settings(root_dir, mod_dir, values, strict=True)
        except Exception as exc:
            messagebox.showerror("保存に失敗しました", "{}\n{}: {}".format(
                config.store_path(runtime), type(exc).__name__, exc), parent=root)
            return False
        saved["values"] = as_shown(values)
        last["values"] = values
        status.configure(text="保存しました {}  {}".format(time.strftime("%H:%M:%S"), config.store_path(runtime)))
        return True

    def reset():
        load_values(dict((k, d["default"]) for k, d in found.items()))
        saved["values"] = as_shown(dict((k, last["values"][k]) for k in found if k in last["values"]))

    def close():
        modtool.save_window(root_dir, mod_dir, root)
        if dirty():
            answer = messagebox.askyesnocancel("未保存の変更", "変更を保存してから閉じますか？", parent=root)
            if answer is None:
                return
            if answer and not save():
                return
        root.destroy()

    ttk.Button(footer, text="閉じる", command=close).pack(side="right")
    ttk.Button(footer, text="保存", style="Accent.TButton", command=save).pack(side="right", padx=(0, 6))
    ttk.Button(footer, text="既定に戻す", command=reset).pack(side="right", padx=(0, 12))
    root.protocol("WM_DELETE_WINDOW", close)
    root._iml_players = players       # 検査が数える（見本の升ごとのコマ）
    return root


def main():
    build_window().mainloop()


if __name__ == "__main__":
    main()
