# -*- coding: utf-8 -*-
r"""依頼の難易度の設定と、街ごとの範囲を決める画面。

    python runtime/mods/318_area_difficulty_growth/tool.py

TECH.md §3.12 の契約で動く。ローダの設定画面（`tools/gui.py`）が `mod.json` の `"tool"` を見て
このファイルを別プロセスで開き、場所は環境変数で渡す。直接起動したときは自分で探す。
`"tool"` を持つ MOD は宣言の設定もこの画面が引き受ける約束なので、タブを2つ置く。

| タブ | 中身 | 保存先 |
|---|---|---|
| 設定 | `mod.json` の宣言の全項目（`modtool.Form`） | `settings\mod_settings.json` |
| 街ごとの範囲 | 世界を選んで街ごとに依頼の難易度の範囲を決める（開発中の `922_town_quest_difficulty` の画面を取り込んだもの） | `state\town_quest_difficulty\<世界名>.json` |

下の「保存」は両方を書く（`modtool.SettingsFooter` の `before_save` / `extra_dirty`）。

街と道は世界の骨格（`worlds\<世界>\world_data.json`）から、今の依頼はセーブ（`saves\<世界>\savedata.json`）から読む。
どちらも読むだけで書かない（骨格はゲームが保存のたびに書く。GAME.md §2.9.1）。
範囲の表と式は `bands.py`（ゲームの中の `town_bands.py` と同じもの）。場所・窓の記憶・配色は `tools/modtool.py`。
"""
import os
import sys

MOD_DIR = os.path.dirname(os.path.abspath(__file__))

_IML_ROOT = os.environ.get("IML_ROOT") or ""
for _tools in ([os.path.join(_IML_ROOT, "tools")] if _IML_ROOT else []) + [
        os.path.normpath(os.path.join(MOD_DIR, os.pardir, os.pardir, os.pardir, "tools"))]:
    # 見つかった最初の候補で止める（両方を先頭へ足すと、後の3つ上が IML_ROOT より先になる）
    if os.path.isfile(os.path.join(_tools, "modtool.py")):
        if _tools not in sys.path:
            sys.path.insert(0, _tools)
        break
if MOD_DIR not in sys.path:
    sys.path.insert(0, MOD_DIR)

import modtool  # noqa: E402
import bands  # noqa: E402  範囲の表と式（素の Python）

#: 街ごとの範囲の控えの置き場（`town_bands.STATE_DIRNAME` と同じ）。
#: フォルダ名から作る `modtool.world_store_path` は使わない（この MOD のフォルダ名とは別の名前のため）。
STATE_DIRNAME = "town_quest_difficulty"

#: 敵のレベルは難易度+1（GAME.md §2.17）。画面の「適正Lv」に使う。
LEVEL_OFFSET = 1

#: 街の規模の表示。ほかの値はそのまま出す。
SIZE_NAMES = {"village": "村", "town": "町", "city": "都市"}


# ----------------------------------------------------------------- 読む
def _saves():
    """`instantale_modloader.saves`。ローダは MOD の2つ上の `runtime` から引く。"""
    return modtool.saves_module(mod_dir=MOD_DIR)


def world_folders(base=""):
    """`[(世界名, フォルダ名), ...]`。骨格を持つ世界だけ。世界名の順。

    セーブを持たない世界（まだ主人公がいない、セーブを退避した）でも範囲を決められるように、骨格の側から並べる。
    """
    saves = _saves()
    root = saves.worlds_dir(base)
    try:
        names = os.listdir(root)
    except OSError:
        return []
    found = []
    for name in names:
        if not os.path.isfile(saves.world_data_path(name, base)):
            continue
        data = saves.read_world(name, base)       # 難読化を解いた骨格。読めなければ None
        found.append((modtool.world_name(data or {}, name, mod_dir=MOD_DIR), name))
    return sorted(found, key=lambda pair: pair[0].casefold())


def load_world(folder, base=""):
    """1世界ぶん。`{"name", "towns", "quests", "has_save"}`。骨格が読めなければ None。

    `quests` は街の id -> 今の依頼の難易度の並び（セーブの依頼。無ければ骨格の依頼）。
    骨格とセーブの読みはローダの `saves.read_world` / `read_save`（どちらも読めなければ None）。
    """
    saves = _saves()
    skeleton = saves.read_world(folder, base)
    if not isinstance(skeleton, dict):
        return None
    save = saves.read_save(folder, base)
    towns = bands.towns_of(skeleton.get("areas") or {})
    source = save if isinstance(save, dict) else skeleton
    quests = {}
    for quest in (source.get("quests") or {}).values():
        if not isinstance(quest, dict):
            continue
        area_id = quest.get("neighboring_settlement_id")
        value = quest.get("difficulty")
        if area_id is None or not isinstance(value, int) or isinstance(value, bool):
            continue
        quests.setdefault(str(area_id), []).append(value)
    return {"name": modtool.world_name(skeleton, folder, mod_dir=MOD_DIR), "towns": towns,
            "quests": quests, "has_save": isinstance(save, dict)}


def store_path(root, state_dir, world):
    """その世界の範囲の控え。ファイル名はゲームの中と同じ関数で作る（§3.11）。"""
    return os.path.join(state_dir, STATE_DIRNAME, modtool.world_filename(root, world, MOD_DIR))


def load_record(path):
    """控えの `(towns, smooth)`。`towns` は id -> `{"lo", "hi", "pinned"}`。"""
    data = modtool.read_json(path)
    towns = data.get("towns") if isinstance(data.get("towns"), dict) else {}
    smooth = data.get("smooth") if isinstance(data.get("smooth"), dict) else {}
    return towns, smooth


def build_record(towns, rows, smooth, limit=bands.GAME_MAX):
    """画面の値から控えを組む。ゲームの範囲（`limit` で収めたもの）と同じで固定していない街は書かない。

    `rows` は id -> `((下限, 上限), 固定)`。並びは id の順（控えの差分を読めるように）。
    """
    out = {}
    for area_id in sorted(rows, key=bands.id_sort_key):
        if area_id not in towns:
            continue
        band, pinned = rows[area_id]
        if not pinned and not bands.changed_from_game(area_id, band, limit):
            continue
        out[area_id] = {"lo": band[0], "hi": band[1], "pinned": bool(pinned)}
    return {"towns": out, "smooth": smooth}


def quests_text(values):
    if not values:
        return "まだ無い"
    low, high = min(values), max(values)
    span = str(low) if low == high else "{}〜{}".format(low, high)
    return "{}（{}件）".format(span, len(values))


def fit_quests(values, limit):
    """今の依頼の最小〜最大を範囲にする。依頼が無ければ None。"""
    if not values:
        return None
    return bands.normalize(min(values), max(values), limit)


def band_text(band):
    return "{}〜{}".format(band[0], band[1])


def beyond_enabled(root):
    """`*_beyond_76` の MOD が置かれていて、読み込み順の無効の一覧に無いか。

    道具は別のプロセスなので窓口 `value_limits` は引けない。置き場と読み込み順から見る。
    """
    mods = os.path.join(root, "runtime", "mods")
    try:
        names = [name for name in os.listdir(mods) if name.endswith("_beyond_76")
                 and os.path.isfile(os.path.join(mods, name, "mod.json"))]
    except OSError:
        return False
    disabled = set()
    for order in ("load_order.json", "load_order.local.json"):
        listed = modtool.read_json(os.path.join(mods, order)).get("disabled")
        if isinstance(listed, list):
            disabled.update(str(name) for name in listed)
    return any(name not in disabled for name in names)


def default_end(limit):
    """最も遠い街の既定。素の id 8 の範囲（67〜76）を、上限まで同じ幅のまま持ち上げたもの。"""
    span = bands.DEFAULT_END[1] - bands.DEFAULT_END[0]
    return limit - span, limit


# ----------------------------------------------------------------- 画面
def build_window(mod_dir=MOD_DIR, base=""):
    """窓を組んで返す（`mainloop()` は呼ばない。検査が中を数えるため）。"""
    import time
    import tkinter as tk
    from tkinter import messagebox, ttk

    win = modtool.SettingsWindow(mod_dir, geometry="1180x720", minsize=(980, 560),
                                 subtitle="「設定」は依頼の難易度の育ちと上限、「街ごとの範囲」は新しく作られる依頼の難易度の範囲",
                                 subtitle_wrap=1120)
    root, root_dir, state_dir = win.root, win.root_dir, win.state_dir
    worlds = world_folders(base)
    beyond = beyond_enabled(root_dir)

    tabs = ttk.Notebook(win.outer)
    tabs.pack(fill="both", expand=True)

    # ---- 設定
    settings_page = ttk.Frame(tabs, padding=(0, 8, 0, 0))
    tabs.add(settings_page, text="設定")
    settings_body = modtool.scrollable(settings_page)
    form = win.form(settings_body, list(win.found))

    def limit():
        """いま画面にある上限（保存前の値も効かせる）。読めなければ保存してある値。"""
        var = form.vars.get("DIFFICULTY_LIMIT")
        text = var.get() if var is not None else ""
        return bands.clamp_limit(text if str(text).strip() else win.current.get("DIFFICULTY_LIMIT", bands.GAME_MAX))

    # ---- 街ごとの範囲
    towns_page = ttk.Frame(tabs, padding=(0, 8, 0, 0))
    tabs.add(towns_page, text="街ごとの範囲")
    limit_label = ttk.Label(towns_page, style="Faint.TLabel", wraplength=1120, justify="left")
    limit_label.pack(anchor="w", pady=(0, 6))

    top = ttk.Frame(towns_page)
    top.pack(fill="x")
    ttk.Label(top, text="世界").pack(side="left")
    world_var = tk.StringVar()
    picker = ttk.Combobox(top, textvariable=world_var, state="readonly", width=32,
                          values=[name for name, _folder in worlds])
    picker.pack(side="left", padx=(6, 12))
    source_label = ttk.Label(top, style="Faint.TLabel")
    source_label.pack(side="left")

    smooth_box = ttk.Frame(towns_page, padding=(0, 8, 0, 4))
    smooth_box.pack(fill="x")
    smooth_vars = {key: tk.StringVar() for key in ("start_lo", "start_hi", "end_lo", "end_hi", "width")}
    for text, keys in (("開始の街", ("start_lo", "start_hi")), ("最も遠い街", ("end_lo", "end_hi"))):
        ttk.Label(smooth_box, text=text).pack(side="left")
        ttk.Entry(smooth_box, textvariable=smooth_vars[keys[0]], width=4, justify="right").pack(side="left", padx=(4, 0))
        ttk.Label(smooth_box, text="〜").pack(side="left")
        ttk.Entry(smooth_box, textvariable=smooth_vars[keys[1]], width=4, justify="right").pack(side="left", padx=(0, 14))
    ttk.Label(smooth_box, text="幅").pack(side="left")
    ttk.Entry(smooth_box, textvariable=smooth_vars["width"], width=4, justify="right").pack(side="left", padx=(4, 14))
    smooth_button = ttk.Button(smooth_box, text="なだらかにする")
    smooth_button.pack(side="left")
    game_button = ttk.Button(smooth_box, text="ゲームの範囲に戻す")
    game_button.pack(side="left", padx=(6, 0))
    fit_button = ttk.Button(smooth_box, text="今の依頼に合わせる")
    fit_button.pack(side="left", padx=(6, 0))
    ttk.Label(towns_page, style="Faint.TLabel", wraplength=1120, justify="left",
              text="なだらかにする: 固定した街と、開始の街・開始から最も遠い街を基準にして、"
                   "ほかの街の範囲を道でつながる街の平均に寄せる。\n"
                   "今の依頼に合わせる: 依頼のある街を、今の依頼の最小〜最大にする。\n"
                   "どのボタンも、固定した街は変えない"
              ).pack(anchor="w")

    over_label = ttk.Label(towns_page, style="Faint.TLabel", foreground="#c0392b")
    over_label.pack(anchor="w", pady=(4, 0))

    body = modtool.scrollable(towns_page)
    table = ttk.Frame(body, padding=(0, 6, 0, 0))
    table.grid(row=0, column=0, sticky="ew")
    headings = ("番号", "名前", "規模", "つながる街", "ゲームの範囲", "今の依頼", "下限", "上限", "固定", "適正Lv")
    for column, heading in enumerate(headings):
        ttk.Label(table, text=heading).grid(row=0, column=column, sticky="w", padx=(0, 10))
    table.columnconfigure(3, weight=1)

    current = {"world": None, "folder": None, "data": None, "rows": {}, "saved": None}

    def clear_rows():
        for child in table.grid_slaves():
            if int(child.grid_info().get("row", 0)) > 0:
                child.destroy()
        current["rows"] = {}

    def row_values(top_limit=None):
        """`{id: ((下限, 上限), 固定)}`。読めない入力の街は入れない。"""
        top_limit = top_limit or limit()
        out = {}
        for area_id, widgets in current["rows"].items():
            band = bands.normalize(widgets["lo"].get(), widgets["hi"].get(), top_limit)
            if band is not None:
                out[area_id] = (band, bool(widgets["pin"].get()))
        return out

    def smooth_values(top_limit=None):
        top_limit = top_limit or limit()

        def number(key, fallback):
            try:
                return int(smooth_vars[key].get())
            except (TypeError, ValueError):
                return fallback
        end = default_end(top_limit)
        return {"start": list(bands.normalize(number("start_lo", bands.DEFAULT_START[0]),
                                              number("start_hi", bands.DEFAULT_START[1]), top_limit)),
                "end": list(bands.normalize(number("end_lo", end[0]), number("end_hi", end[1]), top_limit)),
                "width": max(bands.MIN_WIDTH, min(top_limit, number("width", bands.DEFAULT_WIDTH)))}

    def snapshot():
        """未保存の変更を見るための、入力のままの値。

        上限で収めた値では比べない。上限を変えただけで範囲が変わったことになり、保存のたびに控えの範囲が収め直される。
        """
        rows = sorted((area_id, (w["lo"].get(), w["hi"].get(), bool(w["pin"].get())))
                      for area_id, w in current["rows"].items())
        return rows, sorted((key, var.get()) for key, var in smooth_vars.items())

    def towns_dirty():
        return current["data"] is not None and snapshot() != current["saved"]

    def refresh(*_args):
        top_limit = limit()
        limit_label.configure(text="これから作られる依頼に効く。すでにある依頼は変わらない。"
                                   "範囲を決めていない街はゲームのまま。上限は {}（「設定」の「難易度の上限」）"
                                   .format(top_limit))
        over = False
        for widgets in current["rows"].values():
            band = bands.normalize(widgets["lo"].get(), widgets["hi"].get(), top_limit)
            if band is None:
                widgets["level"].configure(text="（数を入れてください）")
                continue
            widgets["level"].configure(text="{}〜{}".format(band[0] + LEVEL_OFFSET, band[1] + LEVEL_OFFSET))
            over = over or band[1] > bands.GAME_MAX
        if not over:
            over_label.configure(text="")
        elif beyond:
            over_label.configure(text="76 を超えた範囲があります。報酬と品も 76 を超えて伸びます。")
        else:
            over_label.configure(text="76 を超えた分は敵だけが強くなります（923 を入れると報酬と品も伸びます）。")

    def set_band(area_id, band):
        widgets = current["rows"][area_id]
        widgets["lo"].set(str(band[0]))
        widgets["hi"].set(str(band[1]))

    def load(name):
        folder = dict(worlds).get(name)
        data = load_world(folder, base) if folder else None
        clear_rows()
        current.update(world=name, folder=folder, data=data)
        if data is None:
            source_label.configure(text="世界の骨格が読めません。")
            current["saved"] = None
            return
        top_limit = limit()
        record, smooth = load_record(store_path(root_dir, state_dir, data["name"]))
        source_label.configure(text="街 {} か所。依頼は{}から".format(
            len(data["towns"]), "セーブ" if data["has_save"] else "世界の骨格（セーブがありません）"))
        names = dict((area_id, town["name"]) for area_id, town in data["towns"].items())
        for row, area_id in enumerate(sorted(data["towns"], key=bands.id_sort_key), start=1):
            town = data["towns"][area_id]
            entry = record.get(area_id) if isinstance(record.get(area_id), dict) else {}
            band = bands.normalize(entry.get("lo"), entry.get("hi"), top_limit) if entry else None
            band = band or bands.normalize(*bands.game_band(area_id), limit=top_limit)
            widgets = {"lo": tk.StringVar(value=str(band[0])), "hi": tk.StringVar(value=str(band[1])),
                       "pin": tk.BooleanVar(value=bool(entry.get("pinned")))}
            cells = (area_id, town["name"], SIZE_NAMES.get(town["size"], town["size"]),
                     "、".join(names.get(other, other) for other in town["connections"]) or "（道なし）",
                     band_text(bands.game_band(area_id)) + ("" if area_id in bands.SLOT_BANDS else "（全域）"),
                     quests_text(data["quests"].get(area_id)))
            for column, text in enumerate(cells):
                ttk.Label(table, text=text, wraplength=360 if column == 3 else 0).grid(
                    row=row, column=column, sticky="w", padx=(0, 10), pady=(3, 0))
            ttk.Entry(table, textvariable=widgets["lo"], width=5, justify="right").grid(
                row=row, column=6, sticky="w", padx=(0, 6), pady=(3, 0))
            ttk.Entry(table, textvariable=widgets["hi"], width=5, justify="right").grid(
                row=row, column=7, sticky="w", padx=(0, 6), pady=(3, 0))
            ttk.Checkbutton(table, variable=widgets["pin"]).grid(row=row, column=8, sticky="w", pady=(3, 0))
            widgets["level"] = ttk.Label(table, style="Faint.TLabel")
            widgets["level"].grid(row=row, column=9, sticky="w", pady=(3, 0))
            for var in (widgets["lo"], widgets["hi"]):
                var.trace_add("write", refresh)
            current["rows"][area_id] = widgets
        end = default_end(top_limit)
        start_band = smooth.get("start") or bands.DEFAULT_START
        end_band = smooth.get("end") or end
        for key, value in (("start_lo", start_band[0]), ("start_hi", start_band[1]),
                           ("end_lo", end_band[0]), ("end_hi", end_band[1]),
                           ("width", smooth.get("width") or bands.DEFAULT_WIDTH)):
            smooth_vars[key].set(str(value))
        refresh()
        current["saved"] = snapshot()
        footer_status("読み込みました {}".format(time.strftime("%H:%M:%S")))

    def footer_status(text):
        if win.footer is not None:
            win.footer.status.configure(text=text)

    def pick(_event=None):
        name = world_var.get()
        if name == current["world"]:
            return
        if not modtool.confirm_unsaved(root, towns_dirty, win.footer.save, "変更を保存してから続けますか？"):
            world_var.set(current["world"] or "")
            return
        load(name)

    def do_smooth():
        data = current["data"]
        if data is None:
            return
        top_limit = limit()
        values = row_values(top_limit)
        pinned = [area_id for area_id, (_band, pin) in values.items() if pin]
        options = smooth_values(top_limit)
        result = bands.smooth(data["towns"], dict((k, band) for k, (band, _pin) in values.items()),
                              pinned, tuple(options["start"]), tuple(options["end"]),
                              options["width"], top_limit)
        reached = bands.depths(data["towns"], bands.start_of(data["towns"]))
        for area_id, band in result.items():
            if area_id in current["rows"]:
                set_band(area_id, band)
        missing = len(data["towns"]) - len(reached)
        footer_status("なだらかにしました（固定 {} か所{}）。保存すると次の生成から効きます".format(
            len(pinned), "、道でたどり着けない街 {} か所はそのまま".format(missing) if missing else ""))

    def do_game():
        for area_id, widgets in current["rows"].items():
            if not widgets["pin"].get():
                set_band(area_id, bands.game_band(area_id))
        footer_status("固定していない街をゲームの範囲に戻しました。保存すると次の生成から効きます")

    def do_fit():
        data = current["data"]
        if data is None:
            return
        fitted = 0
        for area_id, widgets in current["rows"].items():
            band = fit_quests(data["quests"].get(area_id), limit())
            if band is not None and not widgets["pin"].get():
                set_band(area_id, band)
                fitted += 1
        footer_status("{} か所を今の依頼に合わせました。保存すると次の生成から効きます".format(fitted))

    def save_towns(values):
        """下の「保存」で、設定より先に呼ばれる。範囲に変更が無ければ書かない。False で保存をやめる。"""
        data = current["data"]
        if data is None or not towns_dirty():
            return True
        top_limit = bands.clamp_limit(values.get("DIFFICULTY_LIMIT", bands.GAME_MAX))
        bad = [area_id for area_id, widgets in current["rows"].items()
               if bands.normalize(widgets["lo"].get(), widgets["hi"].get(), top_limit) is None]
        if bad:
            tabs.select(towns_page)
            messagebox.showerror("範囲を確かめてください",
                                 "数でない範囲があります: 番号 " + "、".join(bad), parent=root)
            return False
        rows = row_values(top_limit)
        path = store_path(root_dir, state_dir, data["name"])
        if not modtool.write_json(root_dir, path, build_record(data["towns"], rows, smooth_values(top_limit),
                                                               top_limit)):
            messagebox.showerror("保存に失敗しました", path, parent=root)
            return False
        for area_id, (band, _pin) in rows.items():
            set_band(area_id, band)        # 入力を収めた形（上限・幅）に揃えて見せる
        return True

    def after_save(_values):
        if current["data"] is not None:
            current["saved"] = snapshot()

    def set_values(values):
        form.set(dict((key, values[key]) for key in form.vars))

    win.finish(form.get, set_values, before_save=save_towns, after_save=after_save, extra_dirty=towns_dirty)

    picker.bind("<<ComboboxSelected>>", pick)
    smooth_button.configure(command=do_smooth)
    game_button.configure(command=do_game)
    fit_button.configure(command=do_fit)
    if "DIFFICULTY_LIMIT" in form.vars:
        form.vars["DIFFICULTY_LIMIT"].trace_add("write", refresh)

    if worlds:
        world_var.set(worlds[0][0])
        load(worlds[0][0])
    else:
        refresh()
        footer_status("世界が見つかりません。街ごとの範囲は、ゲームで世界を作ってから決められます")
    root._town_quest_difficulty = current       # 検査が中を読む
    root._area_difficulty_tabs = tabs
    return root


def main():
    build_window().mainloop()


if __name__ == "__main__":
    main()
