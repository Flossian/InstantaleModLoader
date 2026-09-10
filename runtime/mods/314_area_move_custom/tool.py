# -*- coding: utf-8 -*-
"""ワールド別設定画面。

設定画面から選んだワールドの値だけを state/area_move_custom/<世界>.json に保存する。
セーブデータは所持ワールド名の列挙にだけ使い、本文は読まない。
"""

import io
import json
import os
import sys
import tkinter as tk
from tkinter import messagebox, ttk


STATE_DIRNAME = "area_move_custom"
SETTING_DEFAULTS = {
    "WALK_DAYS": 90,
    "COACH_DAYS": 14,
    "COACH_PRICE": 1000,
    "WALK_NAME": "徒歩",
    "COACH_NAME": "馬車",
    "WALK_BUTTON": "{name}({days}日)",
    "COACH_BUTTON": "{name}({price}{short}・{days}日)",
    "WALK_DEPART_TEXT": "{name}で目指す。長旅だ...",
    "COACH_DEPART_TEXT": "{price}{long}を支払った。快適な旅だ...",
    "ARRIVE_TEXT": "",
    "HOP_SCALING": "multiply",
    "HOP_FACTOR": 1.0,
    "HOP_ADD_DAYS": 7,
    "HOP_ADD_FARE": 500,
    "WALK_DAYS_MAX": 90,
    "COACH_DAYS_MAX": 30,
}

FIELD_INFO = (
    ("WALK_DAYS", "徒歩の日数"),
    ("COACH_DAYS", "馬車の日数"),
    ("COACH_PRICE", "馬車の料金"),
    ("WALK_NAME", "徒歩の呼び名"),
    ("COACH_NAME", "馬車の呼び名"),
    ("WALK_BUTTON", "徒歩ボタンの表示"),
    ("COACH_BUTTON", "馬車ボタンの表示"),
    ("WALK_DEPART_TEXT", "徒歩の出発文"),
    ("COACH_DEPART_TEXT", "馬車の出発文"),
    ("ARRIVE_TEXT", "到着文"),
    ("HOP_SCALING", "離れた街への距離補正"),
    ("HOP_FACTOR", "倍加の倍率（1街ごと）"),
    ("HOP_ADD_DAYS", "加算の日数（1街ごと）"),
    ("HOP_ADD_FARE", "加算の料金（1街ごと）"),
    ("WALK_DAYS_MAX", "徒歩の日数の上限"),
    ("COACH_DAYS_MAX", "馬車の日数の上限"),
)

FIELD_NOTES = {
    # 上から見て最初に {short}/{long} が現れる欄へ、単位の出所と既定値を示す。
    "COACH_BUTTON": (
        "{long}:130_currency_unitで変更されている長い通貨単位(規定：ゴールド)\n"
        "{short}:130_currency_unitで変更されている短い通貨単位(規定：G)"
    ),
}


def _loader_root():
    root = os.environ.get("IML_ROOT")
    if root:
        return root
    return os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))


def _state_dir():
    return os.environ.get("IML_STATE_DIR") or os.path.join(_loader_root(), "state")


def _data_dir():
    override = os.environ.get("IML_INSTANTALE_DATA")
    if override:
        return override
    local = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return os.path.join(local, "Darmabeko", "Instantale")


def _add_runtime():
    runtime = os.path.join(_loader_root(), "runtime")
    if runtime not in sys.path:
        sys.path.insert(0, runtime)
    from instantale_modloader import state, write_json
    return state, write_json


def list_worlds():
    """savedata.jsonを持つ所持ワールドのフォルダ名だけを列挙する。"""
    save_root = os.path.join(_data_dir(), "saves")
    try:
        names = os.listdir(save_root)
    except OSError:
        return []
    worlds = []
    for name in names:
        path = os.path.join(save_root, name, "savedata.json")
        if os.path.isfile(path):
            worlds.append({"key": name, "label": name})
    return sorted(worlds, key=lambda item: item["label"].casefold())


def state_path(world):
    state, _write_json = _add_runtime()
    return os.path.join(_state_dir(), STATE_DIRNAME,
                        state.world_filename(world, ".json"))


def load_world_settings(world):
    """保存済みの値を読み、無い値は既定値にする。"""
    values = dict(SETTING_DEFAULTS)
    if not world:
        return values
    try:
        with io.open(state_path(world), encoding="utf-8") as fh:
            record = json.load(fh)
        if not isinstance(record, dict):
            return values
        for key, default in SETTING_DEFAULTS.items():
            value = record.get(key)
            if type(value) is type(default):
                values[key] = value
    except (OSError, ValueError, TypeError):
        pass
    return values


def save_world_settings(world, values):
    """既定値と違う値だけを、このMOD自身のstateへ保存する。"""
    if not world:
        return False
    _state, write_json = _add_runtime()
    record = {key: value for key, value in values.items()
              if value != SETTING_DEFAULTS[key]}
    path = state_path(world)
    if not record:
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
        except OSError:
            return False
        return True
    folder = os.path.dirname(path)
    os.makedirs(folder, exist_ok=True)
    return bool(write_json(path, record, indent=2))


class App:
    """上段のワールド選択に応じて値を切り替える。"""

    def __init__(self, root):
        self.root = root
        self.worlds = list_worlds()
        self.vars = {key: tk.StringVar() for key, _label in FIELD_INFO}
        root.title("ワールド別設定")
        root.minsize(920, 700)

        outer = ttk.Frame(root, padding=12)
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(1, weight=1)

        head = ttk.Frame(outer)
        head.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        head.columnconfigure(1, weight=1)
        ttk.Label(head, text="対象ワールド").grid(row=0, column=0,
                                                   sticky="w", padx=(0, 10))
        labels = [item["label"] for item in self.worlds]
        self.world_var = tk.StringVar(value=labels[0] if labels else "")
        self.world_box = ttk.Combobox(head, textvariable=self.world_var,
                                      values=labels, state="readonly")
        self.world_box.grid(row=0, column=1, sticky="ew")
        self.world_box.bind("<<ComboboxSelected>>", self.changed_world)
        ttk.Label(head, text="保存先はMOD自身のstateです。セーブ本文は変更しません。",
                  style="Sub.TLabel").grid(row=1, column=0, columnspan=2,
                                             sticky="w", pady=(6, 0))

        canvas = tk.Canvas(outer, highlightthickness=0)
        scrollbar = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.grid(row=1, column=0, sticky="nsew")
        scrollbar.grid(row=1, column=1, sticky="ns")
        body = ttk.Frame(canvas, padding=8)
        body.columnconfigure(1, weight=1)
        body.columnconfigure(2, weight=1)
        window_id = canvas.create_window((0, 0), window=body, anchor="nw")
        body.bind("<Configure>", lambda _event: canvas.configure(
            scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda event: canvas.itemconfigure(
            window_id, width=event.width))

        for row, (key, label) in enumerate(FIELD_INFO):
            ttk.Label(body, text=label).grid(row=row, column=0,
                                             sticky="w", padx=(0, 12), pady=5)
            if key == "HOP_SCALING":
                widget = ttk.Combobox(body, textvariable=self.vars[key],
                                      values=("off", "add", "multiply"),
                                      state="readonly")
            else:
                widget = ttk.Entry(body, textvariable=self.vars[key])
            widget.grid(row=row, column=1, sticky="ew", pady=5)
            note = "既定: {}".format(SETTING_DEFAULTS[key])
            extra = FIELD_NOTES.get(key)
            if extra:
                note += "\n" + extra
            ttk.Label(body, text=note, style="Sub.TLabel",
                      justify="left", wraplength=390).grid(
                          row=row, column=2, sticky="w", padx=(12, 0))

        buttons = ttk.Frame(outer)
        buttons.grid(row=2, column=0, columnspan=2, sticky="e", pady=(10, 0))
        ttk.Button(buttons, text="既定値にリセット",
                   command=self.reset).pack(side="left", padx=4)
        ttk.Button(buttons, text="保存", command=self.save).pack(side="left")
        self.load_selected()

    def selected_world_key(self):
        label = self.world_var.get()
        for item in self.worlds:
            if item["label"] == label:
                return item["key"]
        return ""

    def load_selected(self):
        values = load_world_settings(self.selected_world_key())
        for key, var in self.vars.items():
            var.set(str(values[key]))

    def changed_world(self, _event=None):
        self.load_selected()

    def reset(self):
        for key, default in SETTING_DEFAULTS.items():
            self.vars[key].set(str(default))

    def save(self):
        values = {}
        try:
            for key, var in self.vars.items():
                raw = var.get()
                default = SETTING_DEFAULTS[key]
                if type(default) is int:
                    values[key] = int(raw)
                elif type(default) is float:
                    values[key] = float(raw)
                else:
                    values[key] = raw
        except (TypeError, ValueError):
            messagebox.showerror("入力エラー", "日数・料金・倍率には数値を入力してください。")
            return
        if save_world_settings(self.selected_world_key(), values):
            messagebox.showinfo("保存しました", "このワールドの設定を保存しました。")
        else:
            messagebox.showerror("保存できません", "保存に失敗しました。")


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
