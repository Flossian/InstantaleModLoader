# -*- coding: utf-8 -*-
"""`128_item_image_variety` の設定画面。

設定は2段（一括設定とワールド個別設定。ローダの共通の窓 `modtool.build_world_settings_window`）。
その後ろに「画像一覧」と「ジャンル内の重み」のタブを足す。画像一覧は分類（作者の元画像1枚ごと）と部位を選ぶと、その絵を並べる。部位は「全て」も選べる。
画像一覧は見るだけで、何も書かない。キャプション（選ぶときに外見文と比べる英語の一文）は
`build/*_captions.txt` から読み、絵にマウスを載せると出す。
ジャンル内の重みは、既定（`genres.json`）と違う値だけを `state/item_image_genres/weights.json` に書く（本体が読み直す）。
"""
import io
import os
import sys

MOD_DIR = os.path.dirname(os.path.abspath(__file__))
_IML_ROOT = os.environ.get("IML_ROOT") or ""
for _tools in ([os.path.join(_IML_ROOT, "tools")] if _IML_ROOT else []) + [
        os.path.normpath(os.path.join(MOD_DIR, os.pardir, os.pardir, os.pardir, "tools"))]:
    if os.path.isfile(os.path.join(_tools, "modtool.py")) and _tools not in sys.path:
        sys.path.insert(0, _tools)
import modtool  # noqa: E402

def load_categories(mod_dir=MOD_DIR):
    """[(分類, 表示名), ...]（`categories.json`。版10 で同じ種類の元画像を1分類にまとめた）。表示名は「まとまり：分類の名前」。"""
    import json
    try:
        with io.open(os.path.join(mod_dir, "categories.json"), encoding="utf-8") as fh:
            found = json.load(fh)
    except (OSError, ValueError):
        return []
    out = []
    for c in found:
        # 表示名はまとまりの名前と重ねない（「銃：サイバーパンク」。分類がまとまりそのものなら「中世の武器と防具」）
        label = c["label"]
        if label.startswith(c["group"] + "_"):
            label = label[len(c["group"]) + 1:]
        out.append((c["id"], c["group"] if label == c["group"] else "{}：{}".format(c["group"], label)))
    return out


#: 部位（ゲームの `item_detail`）の表示名。並びは画面の並び
FOLDER_LABELS = (
    ("small_weapon", "小型の武器"), ("medium_weapon", "中型の武器"), ("large_weapon", "大型の武器"),
    ("long_weapon", "長物"), ("throwable_weapon", "投擲武器"), ("shield", "盾"),
    ("headgear", "頭"), ("body_armor", "胴鎧"), ("clothing", "服"), ("gauntlets", "手"),
    ("legwear", "脚と足"), ("accessory", "装身具"),
    ("food", "食べ物"), ("drink", "飲み物"), ("medicine", "薬"), ("potion", "薬瓶"), ("plant", "植物"), ("mushroom", "きのこ"), ("tool", "道具"),
    ("relic", "遺物"), ("scrap", "くず"), ("magical_material", "魔法の素材"), ("other_material", "その他の素材"),
    ("creature_part", "魔物の素材"), ("document", "文書"), ("scroll", "巻物"), ("liquid_material", "液体の素材"), ("treasure", "宝"), ("metal", "金属"), ("gem", "宝石"), ("ore", "鉱石"),
)

COLUMNS = 10

#: 画像一覧の絵の背景（表示名, 色）。ゲームの中では暗い地やレア度の色の地に載るので、縁の黒やにじみを見比べられるように切り替える
BACKGROUNDS = (("明るい灰色", "#f0f0f2"), ("黒", "#000000"), ("ゲームの暗い緑", "#1c3a1c"))


def image_root(mod_dir, set_name):
    return os.path.join(mod_dir, "images", set_name, "Assets", "images", "item_candidates_dark")


def gallery_sets(mod_dir=MOD_DIR):
    """[(分類, 表示名, [(部位, 表示名, 枚数), ...]), ...]。絵の無い分類・部位は出さない。"""
    out = []
    for name, label in load_categories(mod_dir):
        root = image_root(mod_dir, name)
        folders = []
        for folder, flabel in FOLDER_LABELS:
            path = os.path.join(root, folder)
            if os.path.isdir(path):
                count = sum(1 for f in os.listdir(path) if f.endswith(".png"))
                if count:
                    folders.append((folder, flabel, count))
        if folders:
            out.append((name, label, folders))
    return out


def list_images(mod_dir, set_name, folder):
    """その分類・部位の絵の [(鍵, パス), ...]。鍵の順。"""
    path = os.path.join(image_root(mod_dir, set_name), folder)
    if not os.path.isdir(path):
        return []
    return [(f[:-4], os.path.join(path, f)) for f in sorted(os.listdir(path)) if f.endswith(".png")]


def load_sources(mod_dir=MOD_DIR):
    """{分類: [(一枚絵の呼び名, 表示名), ...]}。分類にまとめた元の一枚絵（`categories.json` の `sources`）。
    表示名は元の分類の名前から頭のまとまりの名前を外したもの（「食料_菓子」→「菓子」）。"""
    import json
    try:
        with io.open(os.path.join(mod_dir, "categories.json"), encoding="utf-8") as fh:
            found = json.load(fh)
    except (OSError, ValueError):
        return {}
    out = {}
    for c in found:
        out[c["id"]] = [(src["sheet"], src.get("name", src["sheet"]).split("_", 1)[-1]) for src in c.get("sources", [])]
    return out


def key_sheets(mod_dir=MOD_DIR):
    """{鍵: 一枚絵の呼び名}。作り方の表（`build/*.json` の行の `src` の `@` の前）から。表が無ければ空。"""
    import json
    out = {}
    build = os.path.join(mod_dir, "build")
    if not os.path.isdir(build):
        return out
    for name in sorted(os.listdir(build)):
        if not name.endswith(".json"):
            continue
        try:
            with io.open(os.path.join(build, name), encoding="utf-8") as fh:
                rows = json.load(fh)
        except (OSError, ValueError):
            continue
        if isinstance(rows, list):
            for row in rows:
                if isinstance(row, dict) and "key" in row and isinstance(row.get("src"), str):
                    out[row["key"]] = row["src"].partition("@")[0]
    return out


def load_captions(mod_dir=MOD_DIR):
    """{鍵: キャプション}。`build/*_captions.txt`（鍵・部位・キャプションのタブ区切り）から。"""
    captions = {}
    build = os.path.join(mod_dir, "build")
    if not os.path.isdir(build):
        return captions
    for name in sorted(os.listdir(build)):
        if not name.endswith("_captions.txt"):
            continue
        try:
            with io.open(os.path.join(build, name), encoding="utf-8") as fh:
                for line in fh:
                    parts = line.rstrip("\n").split("\t")
                    if len(parts) == 3:
                        captions[parts[0]] = parts[2]
        except OSError:
            continue
    return captions


def gallery_tab(tabs, root_dir):
    """画像一覧のタブを足す（`build_world_settings_window` の `extra_tabs`）。"""
    import tkinter as tk
    from tkinter import ttk

    sets = gallery_sets()
    captions = load_captions()
    sources = load_sources()
    sheet_of = key_sheets()
    page = ttk.Frame(tabs, padding=(6, 8, 6, 6))
    tabs.add(page, text="  画像一覧  ")
    ttk.Label(page, text="絵にマウスを載せると、選ぶときに比べる説明文が出る",
              style="Sub.TLabel", wraplength=860, justify="left").pack(anchor="w", pady=(0, 4))
    picker = ttk.Frame(page)
    picker.pack(fill="x", pady=(0, 4))
    ttk.Label(picker, text="分類", style="Group.TLabel").pack(side="left", padx=(0, 8))
    set_box = ttk.Combobox(picker, state="readonly", width=30, height=30,
                           values=["{}（{}枚）".format(label, sum(c for _f, _l, c in folders))
                                   for _n, label, folders in sets])
    set_box.pack(side="left")
    # 小分類は分類にまとめた元の一枚絵（「全て」か1枚）
    ttk.Label(picker, text="小分類", style="Group.TLabel").pack(side="left", padx=(12, 8))
    sub_box = ttk.Combobox(picker, state="readonly", width=24, height=20)
    sub_box.pack(side="left")
    ttk.Label(picker, text="種類", style="Group.TLabel").pack(side="left", padx=(12, 8))
    folder_box = ttk.Combobox(picker, state="readonly", width=18)
    folder_box.pack(side="left")
    ttk.Label(picker, text="背景", style="Group.TLabel").pack(side="left", padx=(12, 8))
    bg_box = ttk.Combobox(picker, state="readonly", width=12, values=[name for name, _c in BACKGROUNDS])
    bg_box.current(0)
    bg_box.pack(side="left")
    bottom = ttk.Frame(page)
    bottom.pack(side="bottom", fill="x", pady=(6, 0))
    hint = ttk.Label(bottom, style="Faint.TLabel", text="")
    hint.pack(side="left", fill="x", expand=True)
    body = ttk.Frame(page, padding=(0, 0, 6, 0))
    body.pack(fill="both", expand=True)
    grid = modtool._scrollable(body)
    shown = {"photos": [], "cells": [], "subs": [None], "folders": []}

    def background():
        return BACKGROUNDS[max(0, bg_box.current())][1]

    def on_background(_event=None):
        # 並んでいる絵の升だけ塗り替える（並べ直さない）
        for cell in shown["cells"]:
            cell.configure(bg=background())

    def current_set():
        i = set_box.current()
        return sets[i] if 0 <= i < len(sets) else None

    def picked_items(cid, folder):
        """その分類・部位の絵のうち、選んでいる小分類（元の一枚絵）のもの。"""
        i = sub_box.current()
        sheet = shown["subs"][i] if 0 <= i < len(shown["subs"]) else None
        items = list_images(MOD_DIR, cid, folder)
        return items if sheet is None else [(k, p) for k, p in items if sheet_of.get(k) == sheet]

    def on_set(_event=None):
        found = current_set()
        if found is None:
            return
        counts = {}
        for folder, _fl, _c in found[2]:
            for key, _p in list_images(MOD_DIR, found[0], folder):
                counts[sheet_of.get(key)] = counts.get(sheet_of.get(key), 0) + 1
        subs = [(sheet, name) for sheet, name in sources.get(found[0], []) if counts.get(sheet)]
        shown["subs"] = [None] + [sheet for sheet, _n in subs]
        sub_box.configure(values=["全て（{}枚）".format(sum(c for _f, _l, c in found[2]))]
                          + ["{}（{}枚）".format(name, counts[sheet]) for sheet, name in subs])
        sub_box.current(0)
        on_sub()

    def on_sub(_event=None):
        found = current_set()
        if found is None:
            return
        shown["folders"] = []
        for folder, flabel, _count in found[2]:
            n = len(picked_items(found[0], folder))
            if n:
                shown["folders"].append((folder, flabel, n))
        folder_box.configure(values=["全て（{}枚）".format(sum(c for _f, _l, c in shown["folders"]))]
                             + ["{}（{}枚）".format(flabel, count) for _f, flabel, count in shown["folders"]])
        folder_box.current(0)
        on_folder()

    def on_folder(_event=None):
        found = current_set()
        j = folder_box.current()
        if found is None or not (0 <= j <= len(shown["folders"])):
            return
        # 先頭は「全て」。部位ごとに見出しを挟んで並べる
        parts = shown["folders"] if j == 0 else [shown["folders"][j - 1]]
        for child in grid.winfo_children():
            child.destroy()
        shown["photos"] = []
        shown["cells"] = []
        shown["parts"] = []
        row = total = 0
        for folder, flabel, _count in parts:
            items = picked_items(found[0], folder)
            if len(parts) > 1:
                ttk.Label(grid, text="{}（{}枚）".format(flabel, len(items)), style="Group.TLabel").grid(
                    row=row, column=0, sticky="w", pady=(10 if row else 0, 2))
                row += 1
            # 部位ごとに枠を分ける（列の幅は枠の中の絵で決まる。盾・胴鎧の 128px と武器の 64px を同じ列に並べない）
            frame = ttk.Frame(grid)
            frame.grid(row=row, column=0, sticky="w")
            row += 1
            cells, widest = [], 0
            for key, path in items:
                try:
                    photo = tk.PhotoImage(file=path)
                except tk.TclError:
                    continue
                # 幅 32px 以下の絵（素の絵と同じ寸法で作っていた絵）は2倍にして見せる
                if photo.width() <= 32:
                    photo = photo.zoom(2)
                shown["photos"].append(photo)
                widest = max(widest, photo.width())
                # 背景の色を持つ升（tk.Label。ttk の Label は背景を絵ごとに塗れない）
                cell = tk.Label(frame, image=photo, bg=background(), bd=0, padx=4, pady=4)
                cells.append(cell)
                text = "{}  {}".format(key, captions.get(key, ""))
                cell.bind("<Enter>", lambda _e, t=text: hint.configure(text=t))
                cell.bind("<Leave>", lambda _e: hint.configure(text=""))
            shown["cells"] += cells
            shown["parts"].append((cells, widest))
            total += len(cells)
        relayout()
        hint.configure(text="{} 枚".format(total))
        # 分類や部位を替えたら一番上から見せる（`_scrollable` の枠は Canvas の中の Frame）
        grid.master.yview_moveto(0)

    def relayout(_event=None):
        """1行の枚数を、見えている幅と絵の幅から決めて並べ直す（窓を広げなくても横に見切れない）。"""
        width = grid.master.winfo_width()
        if width <= 1:
            width = 900
        if shown.get("width") == width and _event is not None:
            return
        shown["width"] = width
        for cells, widest in shown.get("parts", []):
            # 升の幅は 絵 + 内側の余白 4px×2 + 外側の余白 3px×2
            cols = max(1, (width - 12) // (widest + 14)) if widest else COLUMNS
            for n, cell in enumerate(cells):
                cell.grid(row=n // cols, column=n % cols, padx=3, pady=3, sticky="n")

    set_box.bind("<<ComboboxSelected>>", on_set)
    sub_box.bind("<<ComboboxSelected>>", on_sub)
    folder_box.bind("<<ComboboxSelected>>", on_folder)
    bg_box.bind("<<ComboboxSelected>>", on_background)
    grid.master.bind("<Configure>", relayout, add="+")
    if sets:
        set_box.current(0)
        on_set()
    else:
        hint.configure(text="絵が見つかりません: " + os.path.join(MOD_DIR, "images"))


def body_module():
    """本体（`item_image_variety.py`）。ジャンルの表の読み方と定数を本体と同じものにする。"""
    import importlib.util
    root, _state_dir, _game_dir = modtool.locate(MOD_DIR)
    # `IML_ROOT` の下に `runtime\` が無いときは同梱のローダへ倒れる
    modtool.add_loader_path(root, MOD_DIR)
    spec =importlib.util.spec_from_file_location("item_image_variety_tool", os.path.join(MOD_DIR, "item_image_variety.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def genre_paths():
    """`(既定の表, 変えた値のファイル)`。"""
    body = body_module()
    _root, state_dir, _game_dir = modtool.locate(MOD_DIR)
    return os.path.join(MOD_DIR, "genres.json"), os.path.join(state_dir, body.GENRE_WEIGHTS_DIRNAME, body.GENRE_WEIGHTS_FILE)


def genre_defaults():
    """`(ジャンルの並び [(id, 表示名)], 素の絵の既定, {分類: {ジャンル: 既定}})`。"""
    import json
    with io.open(os.path.join(MOD_DIR, "genres.json"), encoding="utf-8") as fh:
        data = json.load(fh)
    return ([(g["id"], g["label"]) for g in data["genres"]], data.get("stock") or {},
            {cat: dict(table) for cat, table in (data.get("weights") or {}).items()})


def genre_diff(stock, weights, base_stock, base_weights):
    """既定と違う値だけの辞書（変えた値のファイルの中身）。"""
    out = {}
    changed = {g: v for g, v in stock.items() if abs(v - float(base_stock.get(g, 0.0))) > 1e-9}
    if changed:
        out["stock"] = changed
    cats = {}
    for cat, table in weights.items():
        diff = {g: v for g, v in table.items() if abs(v - float(base_weights.get(cat, {}).get(g, 0.0))) > 1e-9}
        if diff:
            cats[cat] = diff
    if cats:
        out["weights"] = cats
    return out


def genre_tab(tabs, root_dir):
    """「ジャンル内の重み」のタブ。分類ごとに、そのジャンルの世界にどれだけ合うか（0〜1）を変える。"""
    import json
    import tkinter as tk
    from tkinter import messagebox, ttk

    body = body_module()
    genres, base_stock, base_weights = genre_defaults()
    _base_path, user_path = genre_paths()
    stock, weights = body.read_genre_weights(MOD_DIR, user_path)
    labels = dict(load_categories())

    page = ttk.Frame(tabs, padding=(6, 8, 6, 6))
    tabs.add(page, text="  ジャンル内の重み  ")
    ttk.Label(page, text="分類がそのジャンルの世界にどれだけ合うか（0〜1）。"
                         "世界ごとのジャンルの重みと掛け合わせて、絵の類似度に足す。保存するとゲームを開いたままでも次の品から効く",
              style="Sub.TLabel", wraplength=860, justify="left").pack(anchor="w", pady=(0, 4))
    bottom = ttk.Frame(page)
    bottom.pack(side="bottom", fill="x", pady=(6, 0))
    status = ttk.Label(bottom, style="Faint.TLabel", text="")
    status.pack(side="left", fill="x", expand=True)
    body_frame = ttk.Frame(page, padding=(0, 0, 6, 0))
    body_frame.pack(fill="both", expand=True)
    grid = modtool._scrollable(body_frame)

    ttk.Label(grid, text="分類", style="Group.TLabel").grid(row=0, column=0, sticky="w", padx=(0, 12))
    for j, (_g, glabel) in enumerate(genres):
        ttk.Label(grid, text=glabel, style="Group.TLabel").grid(row=0, column=j + 1, padx=4)
    cells = {}

    def add_row(row, key, label, table):
        ttk.Label(grid, text=label).grid(row=row, column=0, sticky="w", padx=(0, 12), pady=1)
        for j, (g, _glabel) in enumerate(genres):
            var = tk.StringVar(value="{:g}".format(float(table.get(g, 0.0))))
            box = ttk.Spinbox(grid, from_=0.0, to=1.0, increment=0.1, width=5, textvariable=var)
            box.grid(row=row, column=j + 1, padx=4, pady=1)
            cells[(key, g)] = var

    add_row(1, "", "ゲームの素の絵", stock)
    row = 2
    for cat, _setting in body.IMAGE_SETS:
        add_row(row, cat, labels.get(cat, cat), weights.get(cat, {}))
        row += 1

    def read_cells():
        """`(素の絵, {分類: {ジャンル: 値}})`。読めない値は None を返す（保存しない）。"""
        out_stock, out_weights = {}, {}
        for (key, g), var in cells.items():
            try:
                value = float(var.get())
            except ValueError:
                return None
            if not 0.0 <= value <= 1.0:
                return None
            (out_stock if key == "" else out_weights.setdefault(key, {}))[g] = value
        return out_stock, out_weights

    def save():
        got = read_cells()
        if got is None:
            messagebox.showerror("ジャンル内の重み", "0〜1 の数でない欄があります。", parent=page)
            return
        diff = genre_diff(got[0], got[1], base_stock, base_weights)
        # 別のファイルに書いてから差し替える。本体は品を選ぶたびに読み直すので、書きかけを読ませない
        tmp = user_path + ".tmp"
        try:
            os.makedirs(os.path.dirname(user_path), exist_ok=True)
            with io.open(tmp, "w", encoding="utf-8", newline="\n") as fh:
                json.dump(diff, fh, ensure_ascii=False, indent=1)
            os.replace(tmp, user_path)
        except OSError as e:
            messagebox.showerror("ジャンル内の重み", "保存できませんでした: {}".format(e), parent=page)
            return
        changed = sum(len(t) for t in diff.get("weights", {}).values()) + len(diff.get("stock", {}))
        status.configure(text="保存しました（既定と違う値 {} 件）".format(changed))

    def reset():
        for (key, g), var in cells.items():
            table = base_stock if key == "" else base_weights.get(key, {})
            var.set("{:g}".format(float(table.get(g, 0.0))))
        status.configure(text="既定の値に戻しました（まだ保存していません）")

    ttk.Button(bottom, text="既定に戻す", command=reset).pack(side="right", padx=(6, 0))
    ttk.Button(bottom, text="重みを保存", command=save).pack(side="right")


def failed_tab(tabs, title, error):
    """組めなかったタブの代わり。設定のタブは開けるようにする（例外を窓まで通すと窓ごと開かない）。"""
    from tkinter import ttk
    page = ttk.Frame(tabs, padding=(6, 8, 6, 6))
    tabs.add(page, text="  {}  ".format(title))
    ttk.Label(page, text="このタブを開けませんでした: {!r}".format(error),
              wraplength=860, justify="left").pack(anchor="w")


def extra_tabs(tabs, root_dir):
    for title, build in (("画像一覧", gallery_tab), ("ジャンル内の重み", genre_tab)):
        try:
            build(tabs, root_dir)
        except Exception as e:
            failed_tab(tabs, title, e)


#: 窓の頭の一言。3つの使い方の説明はここに一度だけ置く（分類ごとの欄には枚数だけ）
BLURB = ("足した絵を、同じ種類の元画像をまとめた分類で使い分ける。"
         "「素の絵と混ぜる」はゲームの絵と同じ候補に入れ、「似ているときだけ使う」は"
         "外見文にいちばん似た絵がその分類のときだけ使う。"
         "ジャンルの重みを世界ごとに置くと、そのジャンルに合う分類の絵が選ばれやすくなる。")


def build_window(mod_dir=MOD_DIR):
    return modtool.build_world_settings_window(mod_dir, blurb=BLURB, extra_tabs=extra_tabs)


def main():
    build_window().mainloop()


if __name__ == "__main__":
    main()
