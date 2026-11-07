# -*- coding: utf-8 -*-
"""暦の設定画面（`mod.json` の "tool"）。窓と2段の設定は `tools/modtool.py`。

ここに書くのは `MONTHS` の欄だけ。1行の文字列を、1年の日数・月数・月ごとの表で編集する。
"""

import os
import sys

MOD_DIR = os.path.dirname(os.path.abspath(__file__))

# 共有の土台（`tools/modtool.py`）を import できるようにする（TECH.md §3.12）。
_IML_ROOT = os.environ.get("IML_ROOT") or ""
for _tools in ([os.path.join(_IML_ROOT, "tools")] if _IML_ROOT else []) + [
        os.path.normpath(os.path.join(MOD_DIR, os.pardir, os.pardir, os.pardir, "tools"))]:
    if os.path.isfile(os.path.join(_tools, "modtool.py")) and _tools not in sys.path:
        sys.path.insert(0, _tools)
if MOD_DIR not in sys.path:
    sys.path.insert(0, MOD_DIR)

import date_rules as rules  # noqa: E402
import modtool  # noqa: E402


def small_int(text):
    """欄の数。読めなければ None。"""
    try:
        return int(str(text).strip())
    except ValueError:
        return None


class MonthsEditor(object):
    """`var`（`MONTHS` の文字列）を、1年の日数・月数・月ごとの表で編集する。

    文字列が唯一の値で、表はその写し。入力のたびに文字列へ書き戻す。
    保存のボタンは押しても欄からフォーカスを奪わないので、欄を離れるのを待たない。
    """

    def __init__(self, parent, var):
        import tkinter as tk
        from tkinter import ttk

        self.tk, self.ttk = tk, ttk
        self.var = var
        self.busy = False
        #: 月数を減らしても名前を覚えておく（増やし直したときに戻す）。
        self.names = []
        self.rows = []
        self.frame = ttk.Frame(parent)
        head = ttk.Frame(self.frame)
        head.pack(fill="x", pady=(0, 6))
        self.year_var = tk.StringVar()
        self.count_var = tk.StringVar()
        ttk.Label(head, text="1年の日数").pack(side="left", padx=(0, 6))
        year_box = ttk.Entry(head, textvariable=self.year_var, width=8)
        year_box.pack(side="left", padx=(0, 16))
        ttk.Label(head, text="月数").pack(side="left", padx=(0, 6))
        count_box = ttk.Spinbox(head, textvariable=self.count_var, width=6, from_=1,
                                to=rules.MAX_MONTHS)
        count_box.pack(side="left", padx=(0, 16))
        self.warning = ttk.Label(head, style="Faint.TLabel")
        self.warning.pack(side="left")
        self.table = ttk.Frame(self.frame)
        self.table.pack(fill="x")
        self.table.columnconfigure(1, weight=1)
        ttk.Label(self.table, text="月", style="Faint.TLabel").grid(row=0, column=0, sticky="w", padx=(0, 10))
        ttk.Label(self.table, text="名前（無くてよい）", style="Faint.TLabel").grid(row=0, column=1, sticky="w")
        ttk.Label(self.table, text="日数", style="Faint.TLabel").grid(row=0, column=2, sticky="w", padx=(10, 0))

        self.year_var.trace_add("write", lambda *_args: self.on_year())
        self.count_var.trace_add("write", lambda *_args: self.on_count())
        self.var.trace_add("write", lambda *_args: self.on_value())
        # 途中の値（打ちかけの「3」など）は表へ移さないので、欄を離れるときに表の値へ揃える。
        for box in (year_box, count_box):
            box.bind("<FocusOut>", lambda _event: self.show_totals())
        self.on_value()

    def months(self):
        """いまの表。読めない日数があれば None。"""
        found = []
        for name_var, days_var, _widgets in self.rows:
            days = small_int(days_var.get())
            if days is None or not 1 <= days <= rules.MAX_DAYS_IN_MONTH:
                return None
            found.append((rules.clean_name(name_var.get()), days))
        return found

    def set_row_count(self, count):
        """表の行を `count` 行にする。消す行の名前は `names` に残す。"""
        tk, ttk = self.tk, self.ttk
        while len(self.rows) > count:
            _name, _days, widgets = self.rows.pop()
            for widget in widgets:
                widget.destroy()
        while len(self.rows) < count:
            index = len(self.rows)
            name_var = tk.StringVar(value=self.names[index] if index < len(self.names) else "")
            days_var = tk.StringVar()
            mark = ttk.Label(self.table, text="{}月".format(index + 1))
            mark.grid(row=index + 1, column=0, sticky="w", padx=(0, 10), pady=1)
            name_box = ttk.Entry(self.table, textvariable=name_var)
            name_box.grid(row=index + 1, column=1, sticky="ew", pady=1)
            days_box = ttk.Entry(self.table, textvariable=days_var, width=8)
            days_box.grid(row=index + 1, column=2, sticky="w", padx=(10, 0), pady=1)
            name_var.trace_add("write", lambda *_args, at=index, v=name_var: self.on_name(at, v))
            days_var.trace_add("write", lambda *_args: self.on_month())
            self.rows.append((name_var, days_var, (mark, name_box, days_box)))

    def fill_days(self, days):
        for (_name, days_var, _widgets), value in zip(self.rows, days):
            days_var.set(str(value))

    def write_back(self):
        """表を文字列へ。読めない日数があれば書かない（直前の正しい値が残る）。"""
        months = self.months()
        self.warning.configure(text="" if months else "日数は1以上の数で入れてください")
        if months:
            self.var.set(rules.format_months(months))
            self.show_totals(months)

    def show_totals(self, months=None):
        months = months or self.months()
        if months:
            self.year_var.set(str(sum(days for _name, days in months)))
            self.count_var.set(str(len(months)))
            self.warning.configure(text="")

    def guarded(self, fn):
        """画面から画面への書き込みで、もう一度ここへ戻らないようにする。"""
        if self.busy:
            return
        self.busy = True
        try:
            fn()
        finally:
            self.busy = False

    def on_value(self):
        """文字列が外から変わった（読み込み・既定に戻す）。表を作り直す。"""
        def load():
            months = rules.parse_months(self.var.get()) or rules.parse_months(rules.DEFAULT_MONTHS)
            self.names = [name for name, _days in months]
            for row in self.rows:
                row[0].set("")
            self.set_row_count(len(months))
            for (name_var, days_var, _widgets), (name, days) in zip(self.rows, months):
                name_var.set(name)
                days_var.set(str(days))
            self.show_totals(months)
        self.guarded(load)

    def on_name(self, index, name_var):
        def update():
            while len(self.names) <= index:
                self.names.append("")
            self.names[index] = rules.clean_name(name_var.get())
            self.write_back()
        self.guarded(update)

    def on_month(self):
        self.guarded(self.write_back)

    def redistribute(self, total, count):
        """`total` 日を `count` か月へ均等に割り振る。入らない組み合わせは表を動かさない。"""
        if total is None or count is None or not 1 <= count <= rules.MAX_MONTHS:
            self.warning.configure(text="月数は1から{}までの数で入れてください".format(rules.MAX_MONTHS))
            return
        if total < count or total > count * rules.MAX_DAYS_IN_MONTH:
            self.warning.configure(text="1年の日数は月数より少なくできません")
            return
        self.set_row_count(count)
        self.fill_days(rules.distribute(total, count))
        months = self.months()
        self.var.set(rules.format_months(months))
        self.warning.configure(text="")

    def on_year(self):
        self.guarded(lambda: self.redistribute(small_int(self.year_var.get()), len(self.rows)))

    def on_count(self):
        def update():
            months = self.months()
            total = sum(days for _name, days in months) if months else None
            self.redistribute(total, small_int(self.count_var.get()))
        self.guarded(update)


def months_editor(parent, var):
    """`modtool` の差し込み口。入力欄の代わりに置く部品を返す。"""
    return MonthsEditor(parent, var).frame


def build_window(mod_dir=MOD_DIR):
    return modtool.build_world_settings_window(mod_dir, editors={"MONTHS": months_editor})


def main():
    build_window().mainloop()


if __name__ == "__main__":
    main()
