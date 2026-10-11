# -*- coding: utf-8 -*-
r"""街の出来事の設定画面。設定を役割ごとのタブに分け、一覧の見本と控えの様子も見せる。

    python runtime/mods/918_town_incidents/tool.py

TECH.md §3.12 の契約で動く。ローダの設定画面（`tools/gui.py`）が `mod.json` の `"tool"` を見て
このファイルを別プロセスで開き、場所は環境変数で渡す。直接起動したときは自分で探す。

なぜ独自の画面か: 宣言の設定は 30 項目あり、半分（15）が出来事ごとの ON/OFF。
1列に並べると、起こり方・段の進み方・一覧の見え方の設定が出来事の並びに埋もれる。

| タブ | 中身 | 書く先 |
|---|---|---|
| 起こり方 / 進み方 / 値段と文章 / 一覧 / 出来事の種類 | 宣言の設定。一覧のタブには、選んだ出し方で一覧の見本を出す | `settings/mod_settings.json`（`modtool.save_settings`） |
| 記録 | `state\town_incidents\` の控えを読んで並べる。控えを消すボタン | 読むだけ（消すときだけファイルを消す） |

一括設定だけ（MOD 本体がモジュールのグローバルしか読まない。`911_` と同じ）。
項目の名前・型・既定値・上下限は `mod.json` の `"settings"` が唯一の出所（`modtool.decls`）。
"""
import importlib.util
import os
import sys

MOD_DIR = os.path.dirname(os.path.abspath(__file__))

# 共有の土台（`tools/modtool.py`）を import できるようにする（`911_` の tool.py と同じ探し方）。
_IML_ROOT = os.environ.get("IML_ROOT") or ""
for _tools in ([os.path.join(_IML_ROOT, "tools")] if _IML_ROOT else []) + [
        os.path.normpath(os.path.join(MOD_DIR, os.pardir, os.pardir, os.pardir, "tools"))]:
    if os.path.isfile(os.path.join(_tools, "modtool.py")) and _tools not in sys.path:
        sys.path.insert(0, _tools)

import modtool  # noqa: E402


def _load_rules():
    """MOD の規則（`incidents.py`）。ゲームにもローダにも依らないので、ここでも読める。"""
    spec = importlib.util.spec_from_file_location("town_incidents_tool_rules",
                                                  os.path.join(MOD_DIR, "incidents.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


rules = _load_rules()

#: 控えのフォルダ（本体の `STATE_DIRNAME`）。
STATE_DIRNAME = "town_incidents"

#: タブごとの項目と説明。
TABS = (
    ("起こり方", ("ROLL_DAYS", "ROLL_PERCENT", "MAX_ACTIVE", "REST_DAYS", "SIZE_WEIGHTING"),
     "日数が進むと、通算で抽選の間隔が経つたびに世界全体で1回だけ抽選し、当たれば1つ起きる。\n"
     "起きるのは、訪れたことがありギルドのある村・町・都市だけ。1つの街に起きるのは1つまで。"),
    ("進み方", ("STAGE_DAYS", "SETTLE_DAYS", "STEPS_PER_ELAPSE"),
     "放っておくと 兆し → 悪化 → 深刻 と段階が進み、深刻からしばらくで自然に収まる。\n"
     "日数が進むのは宿泊・街の移動・自由行動だけ（依頼の最中は進まない）。宿の1泊は30日、徒歩の移動は90日。"),
    ("値段と文章", ("PRICE_STRENGTH_PERCENT", "ARRIVAL_NOTICE", "GUIDE_NOTICE", "SHOP_NOTICE",
                    "USE_LLM", "FIT_CHECK"),
     "出来事の起きている街に居る間だけ、値段と宿代が動く（セーブには残らない）。\n"
     "呼び名や街の様子の文章は、起きたときにLLMがその世界の言葉で1回だけ書く。"),
    ("一覧", ("WINDOW_DAYS", "WINDOW_EFFECTS"),
     "画面左下の注意の標識のアイコンから開く「各地の出来事」の一覧の見え方。下の見本は、いまの欄の値で描いている。"),
    ("出来事の種類", tuple("KIND_" + kind["key"].upper() for kind in rules.KINDS),
     "チェックの付いた出来事だけが起きる。外すと新しく起きなくなる（起きている出来事はそのまま進む）。\n"
     "呼び名と中身は世界ごとにLLMが書く（中世なら盗賊団、西部劇なら無法者の一味、のように）。"),
)

#: 一覧の見本に使う出来事（呼び名は表の名前。LLM の文は使わない）。日付は 430 日目の世界。
SAMPLE_DAY = 430
SAMPLE_EVENTS = (
    {"id": 1, "area_name": "灰の都", "kind": "counterfeit", "stage": 3, "status": rules.ACTIVE,
     "started": 280, "stage_day": 400, "quest": None, "text": {}},
    {"id": 2, "area_name": "霧の港町", "kind": "epidemic", "stage": 2, "status": rules.ACTIVE,
     "started": 355, "stage_day": 415, "quest": "57", "text": {}},
    {"id": 3, "area_name": "北の村", "kind": "shortage", "stage": 1, "status": rules.ACTIVE,
     "started": 420, "stage_day": 420, "quest": None, "text": {}},
    {"id": 4, "area_name": "岩の町", "kind": "boom", "stage": 2, "status": rules.ENDED,
     "started": 300, "stage_day": 360, "ended": 380, "end_by": rules.END_PLAYER, "text": {}},
)


def setting_keys():
    return [key for _name, keys, _blurb in TABS for key in keys]


def number(value, default):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def preview_lines(values):
    """一覧の見本の行。`values` はいまの欄の値（文字列のままでよい）。"""
    return rules.view_lines([dict(event) for event in SAMPLE_EVENTS], SAMPLE_DAY, {},
                            stage_days=number(values.get("STAGE_DAYS"), 60),
                            settle_days=number(values.get("SETTLE_DAYS"), 90),
                            ended_days=360,
                            in_words=(values.get("WINDOW_DAYS") == rules.DAYS_VAGUE),
                            effects=values.get("WINDOW_EFFECTS") or rules.EFFECTS_NUMBER,
                            strength_percent=number(values.get("PRICE_STRENGTH_PERCENT"), 100))


# ---------------------------------------------------------------- 様子
def state_files(state_dir):
    """控えのファイル `[(表示名, パス)]`。新しく書かれた順。"""
    folder = os.path.join(state_dir, STATE_DIRNAME)
    try:
        names = [name for name in os.listdir(folder) if name.endswith(".json")]
    except OSError:
        return []
    stamped = []
    for name in names:
        path = os.path.join(folder, name)
        try:
            stamped.append((os.path.getmtime(path), path))
        except OSError:
            continue
    stamped.sort(reverse=True)
    return [(os.path.basename(path)[:-5], path) for _mtime, path in stamped]


def describe(bucket, values):
    """控え1つを読む用の文にする。一覧と同じ行に、一覧には出さない控えの中身を足す。"""
    if not isinstance(bucket, dict):
        return "（読めませんでした）"
    events = rules.events_of(bucket)
    days = [day for event in events for day in (event.get("stage_day"), event.get("ended"))
            if isinstance(day, int)]
    day = max(days) if days else None
    lines = ["記録の中で最後に動いた日: {}日目（ゲームの今日ではない）".format(day if day is not None else "?"),
             "次の抽選: {}".format("{}日目".format(bucket["next_roll_day"])
                                   if isinstance(bucket.get("next_roll_day"), int)
                                   else "まだ数え始めていない（次に日数が進んだときから数える）"), ""]
    lines.extend(rules.view_lines(events, day, {},
                                  stage_days=number(values.get("STAGE_DAYS"), 60),
                                  settle_days=number(values.get("SETTLE_DAYS"), 90),
                                  ended_days=10 ** 6, in_words=False,
                                  effects=rules.EFFECTS_NUMBER,
                                  strength_percent=number(values.get("PRICE_STRENGTH_PERCENT"), 100))
                 or ["出来事はまだ起きていない。"])
    pending = rules.events_of(bucket, rules.PENDING)
    if pending:
        lines.append("")
        lines.append("文章を用意している途中（まだ影響は出ていない）: {}".format(
            "、".join("{}の{}".format(event.get("area_name"), rules.text_of(event, "name"))
                     for event in pending)))
    cooldown = bucket.get("cooldown") or {}
    if cooldown:
        lines.append("")
        names = dict((str(event.get("area")), event.get("area_name")) for event in events
                     if event.get("area_name"))
        lines.append("次が起きるまで休んでいる街: {}".format(
            "、".join("{}（{}日目まで）".format(names.get(str(area)) or "土地{}".format(area), until)
                     for area, until in sorted(cooldown.items()))))
    cleanup = bucket.get("cleanup") or []
    if cleanup:
        lines.append("片付ける予定の依頼（受けている最中だったもの）: {}".format("、".join(map(str, cleanup))))
    return "\n".join(lines)


# ---------------------------------------------------------------- 設定画面
def build_window(mod_dir=MOD_DIR):
    """設定画面を組んで返す（`mainloop()` は呼ばない。検査が中を数えるため）。"""
    import time
    import tkinter as tk
    from tkinter import messagebox, ttk

    info = modtool.manifest(mod_dir)
    title = (info.get("name") or {}).get("ja") or modtool.mod_name(mod_dir)
    root_dir, state_dir, _game_dir = modtool.locate(mod_dir)
    runtime = os.path.join(root_dir, "runtime")
    config = modtool.config_module(root_dir, mod_dir)
    found = modtool.decls(mod_dir, root_dir)
    current = modtool.load_settings(root_dir, mod_dir)
    handled = set(setting_keys())
    missing = [key for key in handled if key not in found]
    unplaced = [key for key in found if key not in handled]

    root = tk.Tk()
    root.title(title)
    root.minsize(860, 600)
    modtool.restore_window(root_dir, mod_dir, root, "1000x760")
    theme = modtool.setup_theme(root, root_dir)
    palette = getattr(theme, "PALETTE", None) if theme is not None else None
    text_style = {} if not isinstance(palette, dict) else {
        "background": palette.get("surface"), "foreground": palette.get("text"),
        "insertbackground": palette.get("text"), "relief": "flat", "bd": 0,
        "highlightthickness": 1, "highlightbackground": palette.get("control_edge"),
        "highlightcolor": palette.get("control_edge"), "padx": 8, "pady": 6}
    text_style = dict((k, v) for k, v in text_style.items() if v is not None)

    outer = ttk.Frame(root, padding=12)
    outer.pack(fill="both", expand=True)
    ttk.Label(outer, text=title, style="Title.TLabel").pack(anchor="w")
    ttk.Label(outer, style="Sub.TLabel", wraplength=940, justify="left",
              text="設定はどの世界にも効く（settings\\mod_settings.json）。ゲームを起動し直すと効く。\n"
                   "記録のタブは出来事の記録（state\\town_incidents\\）を読むだけ。"
              ).pack(anchor="w", pady=(0, 8))

    footer = ttk.Frame(outer)
    footer.pack(side="bottom", fill="x")
    ttk.Separator(outer).pack(side="bottom", fill="x", pady=8)

    notebook = ttk.Notebook(outer)
    notebook.pack(fill="both", expand=True)

    forms = []
    preview = {"box": None}

    def everything():
        values = {}
        for form in forms:
            values.update(form.get())
        for key in unplaced:
            values[key] = current.get(key, found[key]["default"])
        return values

    def show_preview(*_args):
        box = preview["box"]
        if box is None:
            return
        box.configure(state="normal")
        box.delete("1.0", "end")
        box.insert("1.0", "各地の出来事\n\n" + "\n".join(preview_lines(everything())))
        box.configure(state="disabled")

    for name, keys, blurb in TABS:
        page = ttk.Frame(notebook, padding=(4, 8, 4, 4))
        notebook.add(page, text=name)
        ttk.Label(page, text=blurb, style="Sub.TLabel", wraplength=920, justify="left").pack(
            anchor="w", pady=(0, 6))
        if name == "一覧":
            holder = ttk.Frame(page)
            holder.pack(fill="x")
            form = modtool._Form(holder, dict((key, found[key]) for key in keys if key in found),
                                 lambda k: "既定: " + modtool.shown(found[k]["default"], found[k]))
            forms.append(form)
            ttk.Label(page, text="見本", style="Group.TLabel").pack(anchor="w", pady=(12, 4))
            box = tk.Text(page, wrap="char", height=16, **text_style)
            box.pack(fill="both", expand=True)
            preview["box"] = box
            continue
        body = modtool._scrollable(page)
        body.columnconfigure(1, weight=1)
        forms.append(modtool._Form(body, dict((key, found[key]) for key in keys if key in found),
                                   lambda k: "既定: " + modtool.shown(found[k]["default"], found[k])))

    # 見本は、一覧の見え方に効く欄が変わるたびに描き直す（段の日数と強さも一覧の文に効く）。
    for form in forms:
        for key, var in form.vars.items():
            if key in ("WINDOW_DAYS", "WINDOW_EFFECTS", "PRICE_STRENGTH_PERCENT", "STAGE_DAYS",
                       "SETTLE_DAYS"):
                var.trace_add("write", show_preview)

    # ---- 様子
    page = ttk.Frame(notebook, padding=(4, 8, 4, 4))
    notebook.add(page, text="記録")
    ttk.Label(page, style="Sub.TLabel", wraplength=920, justify="left",
              text="出来事の記録（世界と主人公の組ごとに1つ）。一覧と同じ行を、日数と数字で並べる。\n"
                   "ゲームを起動している間は、ファイルが少し古いことがある。\n"
                   "「記録を消す」はゲームを閉じてから使う（起動中に消すと、次のセーブで書き戻される）。"
              ).pack(anchor="w", pady=(0, 6))
    picker_row = ttk.Frame(page)
    picker_row.pack(fill="x")
    ttk.Label(picker_row, text="記録").pack(side="left", padx=(0, 8))
    picked = tk.StringVar()
    picker = ttk.Combobox(picker_row, textvariable=picked, state="readonly", width=60)
    picker.pack(side="left", fill="x", expand=True)
    view = tk.Text(page, wrap="char", height=20, **text_style)
    view.pack(fill="both", expand=True, pady=(8, 0))
    status_files = {"rows": []}

    def show_state(*_args):
        path = dict(status_files["rows"]).get(picked.get())
        view.configure(state="normal")
        view.delete("1.0", "end")
        if path is None:
            view.insert("1.0", "記録はまだありません（日数を進めた世界から作られる）。")
        else:
            view.insert("1.0", describe(modtool.read_json(path), everything()) + "\n\n" + path)
        view.configure(state="disabled")

    def reload_state():
        status_files["rows"] = state_files(state_dir)
        names = [name for name, _path in status_files["rows"]]
        picker.configure(values=names)
        if picked.get() not in names:
            picked.set(names[0] if names else "")
        show_state()

    def delete_state():
        path = dict(status_files["rows"]).get(picked.get())
        if path is None:
            return
        if not messagebox.askyesno(
                "記録を消す", "「{}」の記録を消します。\n起きている出来事も、終わった出来事も消えます"
                "（ゲームが作らせた依頼は掲示板に残ります）。\nゲームは閉じていますか？".format(picked.get()),
                parent=root):
            return
        try:
            os.remove(path)
        except OSError as exc:
            messagebox.showerror("消せませんでした", "{}\n{}".format(path, exc), parent=root)
        reload_state()

    picker.bind("<<ComboboxSelected>>", show_state)
    buttons = ttk.Frame(page)
    buttons.pack(fill="x", pady=(6, 0))
    ttk.Button(buttons, text="読み直す", command=reload_state).pack(side="left")
    ttk.Button(buttons, text="記録を消す", command=delete_state).pack(side="right")

    # ---- 値の出し入れ（`911_` の tool.py と同じ）
    def as_shown(values):
        return dict((k, v if isinstance(v, bool) else str(v)) for k, v in values.items())

    saved = {"values": {}}
    last = {"values": current}

    def load_values(values):
        for form in forms:
            form.set(dict((k, values[k]) for k in form.vars))
        saved["values"] = as_shown(dict((k, values[k]) for k in found))
        show_preview()

    load_values(current)
    reload_state()

    def dirty():
        return as_shown(dict((k, v) for k, v in everything().items() if k in found)) != saved["values"]

    status = ttk.Label(footer, style="Faint.TLabel", text="ゲームを起動し直すと効きます"
                       + ("  ※ 宣言が無い項目: " + ", ".join(missing) if missing else ""))
    status.pack(side="left")

    def save():
        values, bad = modtool.coerce_all(mod_dir, everything(), root_dir)
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
        status.configure(text="保存しました {}  {}".format(
            time.strftime("%H:%M:%S"), config.store_path(runtime)))
        return True

    def reset():
        load_values(dict((k, d["default"]) for k, d in found.items()))
        saved["values"] = as_shown(dict((k, last["values"][k]) for k in found
                                        if k in last["values"]))

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
    return root


def main():
    build_window().mainloop()


if __name__ == "__main__":
    main()
