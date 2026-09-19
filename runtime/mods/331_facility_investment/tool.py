# -*- coding: utf-8 -*-
r"""施設の設定画面。種類ごとの建設費・1日の売上・最低の規模を**表で**編集する。

    python runtime/mods/331_facility_investment/tool.py

TECH.md §3.12 の契約で動く。ローダの設定画面（`tools/gui.py`）が `mod.json` の `"tool"` を見て
このファイルを別プロセスで開き、場所は環境変数で渡す。直接起動したときは自分で探す。

なぜ独自の画面か: 宣言の設定は 23 項目（種類6 × 3 と共通5）で、1列に並べると読めない
（本人の指摘「この数まで行くと独自設定 GUI を実装した方がいい」）。
種類を行、項目を列にした表なら 6 行で収まり、隣に「都市・最上ではいくらか」も出せる。

| 画面 | 書く先 |
|---|---|
| 種類ごとの表（建設費・1日の売上・最低の規模） | `settings/mod_settings.json`（`instantale_modloader.config` 経由。既定と同じ値は書かない） |
| 共通の5項目（倍率・溜めておける日数・主人・部屋） | 同じ |

**一括設定だけ。** ワールド個別設定のタブは出さない。MOD 本体（`facility_investment.py`）が
モジュールのグローバルしか読まないので、出しても効かない。効かない画面は出さない。
要るときは本体に読み口を足してから（`130_` / `314_` の形。TECH.md §3.12.1）。

項目の名前・型・既定値・上下限・選択肢は `mod.json` の `"settings"` が唯一の出所
（`modtool.decls`）。値札の式は MOD の `catalog.py` をそのまま import して使う
（本体は import しない。`catalog` は素の Python で、ローダにもゲームにも依存しない）。
場所・設定の読み書き・窓の記憶・配色は `tools/modtool.py`（どの道具も同じ）。
"""
import os
import sys

MOD_DIR = os.path.dirname(os.path.abspath(__file__))

# 共有の土台（`tools/modtool.py`）を import できるようにする。
# `IML_ROOT` が指す先に `tools/` が無いこと（オフラインの検査）と、
# 環境変数の無い直接起動の両方があるので、3つ上も候補に入れる。
_IML_ROOT = os.environ.get("IML_ROOT") or ""
for _tools in ([os.path.join(_IML_ROOT, "tools")] if _IML_ROOT else []) + [
        os.path.normpath(os.path.join(MOD_DIR, os.pardir, os.pardir, os.pardir, "tools"))]:
    if os.path.isfile(os.path.join(_tools, "modtool.py")) and _tools not in sys.path:
        sys.path.insert(0, _tools)
if MOD_DIR not in sys.path:
    sys.path.insert(0, MOD_DIR)

import modtool  # noqa: E402
import catalog  # noqa: E402  MOD の表と式（素の Python）

#: 表の列。`(接頭辞, 見出し)`。設定名は `<接頭辞>_<種類を大文字にしたもの>`（本体の `setting_of` と同じ規則）。
COLUMNS = (("COST", "建設費（並・町）"), ("INCOME", "1日の売上（並・町）"), ("MIN_SIZE", "最低の規模"))
#: 参考の列に出す組み合わせ。いちばん高い値札（都市・最上）。
PREVIEW_TIER, PREVIEW_SIZE = "advanced", "city"


def table_key(prefix, kind):
    """表の1マスに対応する設定名。"""
    return "{}_{}".format(prefix, str(kind).upper())


def table_keys():
    """表が受け持つ設定名（種類の順、列の順）。"""
    return [table_key(prefix, kind) for kind in catalog.KIND_ORDER for prefix, _title in COLUMNS]


def general_keys(found):
    """表に入らない設定名（宣言の順）。共通の5項目。"""
    table = set(table_keys())
    return [key for key in found if key not in table]


def preview_price(kind, values):
    """その種類の都市・最上の値札と1日の売上。`values` は画面の値（文字列）。

    打ちかけの値（空・数でない）のときは表の値で計算する
    （`catalog._base` が読めない値を表へ落とす）。倍率も画面の値を使う。
    """
    def number(key, fallback):
        try:
            return int(values.get(key))
        except (TypeError, ValueError):
            return fallback

    scale_cost = number("COST_SCALE", 100)
    scale_income = number("INCOME_SCALE", 100)
    cost = catalog.cost_of(kind, PREVIEW_TIER, PREVIEW_SIZE, scale_cost,
                           base=values.get(table_key("COST", kind)))
    income = catalog.income_per_day(kind, PREVIEW_TIER, PREVIEW_SIZE, scale_income,
                                    base=values.get(table_key("INCOME", kind)))
    return cost, income


def preview_text(kind, values):
    cost, income = preview_price(kind, values)
    if cost is None:
        return ""
    return "都市・最上: {:,}G / 1日 {:,}G".format(cost, income)


def merge(table_values, general_values):
    """表と共通の入力を、保存に渡す1つの辞書にする。"""
    out = dict(table_values)
    out.update(general_values)
    return out


def build_window(mod_dir=MOD_DIR):
    """窓を組んで返す（`mainloop()` は呼ばない。検査が中を数えるため）。"""
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
    missing = [key for key in table_keys() if key not in found]

    root = tk.Tk()
    root.title(title)
    root.minsize(820, 560)
    modtool.restore_window(root_dir, mod_dir, root, "980x680")
    modtool.setup_theme(root, root_dir)

    outer = ttk.Frame(root, padding=12)
    outer.pack(fill="both", expand=True)
    ttk.Label(outer, text=title, style="Title.TLabel").pack(anchor="w")
    ttk.Label(outer, style="Sub.TLabel", wraplength=920, justify="left",
              text="この画面で、種類ごとの建設費・1日の売上・建てられる最低の規模と、共通の5項目を決める。"
                   "額は並の等級を町に建てるときのもの。等級と街の規模と倍率はこの上に掛かる"
                   "（右の参考は都市・最上のとき）。どの世界でも効く（settings\\mod_settings.json）"
              ).pack(anchor="w", pady=(0, 8))

    footer = ttk.Frame(outer)
    footer.pack(side="bottom", fill="x")
    ttk.Separator(outer).pack(side="bottom", fill="x", pady=8)

    body = modtool._scrollable(outer)
    body.columnconfigure(0, weight=1)

    # ---- 種類ごとの表
    ttk.Label(body, text="種類ごと", style="Group.TLabel").grid(row=0, column=0, sticky="w")
    table = ttk.Frame(body, padding=(0, 4, 0, 10))
    table.grid(row=1, column=0, sticky="ew")
    for column in range(1, 5):
        table.columnconfigure(column, weight=1 if column < 4 else 2)
    ttk.Label(table, text="種類").grid(row=0, column=0, sticky="w", padx=(0, 12))
    for column, (_prefix, heading) in enumerate(COLUMNS, start=1):
        ttk.Label(table, text=heading).grid(row=0, column=column, sticky="w", padx=(0, 8))
    ttk.Label(table, text="参考（いちばん高い組み合わせ）").grid(row=0, column=4, sticky="w")

    table_vars = {}
    previews = {}
    for row, kind in enumerate(catalog.KIND_ORDER, start=1):
        spec = catalog.kind_of(kind)
        ttk.Label(table, text=spec["label"]).grid(row=row, column=0, sticky="w",
                                                    padx=(0, 12), pady=(4, 0))
        for column, (prefix, _heading) in enumerate(COLUMNS, start=1):
            key = table_key(prefix, kind)
            decl = found.get(key)
            var = tk.StringVar()
            if decl is None:
                ttk.Label(table, text="（宣言がありません）", style="Faint.TLabel").grid(
                    row=row, column=column, sticky="w", pady=(4, 0))
            elif decl["type"] == "choice":
                ttk.Combobox(table, textvariable=var, values=decl["values"], width=10,
                             state="readonly").grid(row=row, column=column, sticky="ew",
                                                    padx=(0, 8), pady=(4, 0))
            else:
                ttk.Entry(table, textvariable=var, width=12, justify="right").grid(
                    row=row, column=column, sticky="ew", padx=(0, 8), pady=(4, 0))
            table_vars[key] = var
        previews[kind] = ttk.Label(table, style="Faint.TLabel")
        previews[kind].grid(row=row, column=4, sticky="w", pady=(4, 0))
    note = ("既定は " + " / ".join("{} {:,}G・{:,}G".format(
        catalog.kind_of(k)["label"], catalog.kind_of(k)["cost"], catalog.kind_of(k)["income"])
        for k in catalog.KIND_ORDER))
    ttk.Label(table, text=note, style="Faint.TLabel", wraplength=900, justify="left").grid(
        row=len(catalog.KIND_ORDER) + 1, column=0, columnspan=5, sticky="w", pady=(6, 0))

    # ---- 共通の項目（宣言駆動の入力欄をそのまま借りる）
    ttk.Label(body, text="共通", style="Group.TLabel").grid(row=2, column=0, sticky="w")
    common = ttk.Frame(body, padding=(0, 4, 6, 0))
    common.grid(row=3, column=0, sticky="ew")
    general = dict((key, found[key]) for key in general_keys(found))
    general_form = modtool._Form(common, general, lambda k: "既定: " + modtool.shown(found[k]["default"]))

    def as_shown(values):
        return dict((k, v if isinstance(v, bool) else str(v)) for k, v in values.items())

    def table_get():
        return dict((key, var.get()) for key, var in table_vars.items())

    def table_set(values):
        for key, var in table_vars.items():
            var.set(str(values.get(key, "")))

    def everything():
        return merge(table_get(), general_form.get())

    def refresh_previews(*_args):
        values = everything()
        for kind, label in previews.items():
            label.configure(text=preview_text(kind, values))

    for var in list(table_vars.values()) + list(general_form.vars.values()):
        var.trace_add("write", refresh_previews)

    saved = {"values": {}}

    def load_values(values):
        table_set(values)
        general_form.set(dict((k, values[k]) for k in general))
        saved["values"] = as_shown(dict((k, values[k]) for k in found))
        refresh_previews()

    load_values(current)

    def dirty():
        return as_shown(everything()) != saved["values"]

    status = ttk.Label(footer, style="Faint.TLabel", text="次の注入から効きます"
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
        status.configure(text="保存しました {}  {}".format(
            time.strftime("%H:%M:%S"), config.store_path(runtime)))
        return True

    def reset():
        load_values(dict((k, d["default"]) for k, d in found.items()))
        saved["values"] = as_shown(current)         # 「既定に戻す」は未保存の変更のまま

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
