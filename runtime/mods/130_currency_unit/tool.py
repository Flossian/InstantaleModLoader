# -*- coding: utf-8 -*-
"""130 のワールド別設定画面。

ローダ標準の設定欄は固定項目しか扱えないため、この画面を使う。
上段の世界選択を変えると、その世界の控え、または既定値を表示する。
セーブデータは読むだけで、書くのは自分の state/currency_unit だけ。
"""

import io
import json
import os
import sys
import tkinter as tk
from tkinter import messagebox, ttk

# manifestのMOD ID（130_currency_unit）と、stateの保存フォルダ名は別物。
# stateは共有部品の約束どおり番号なしの名前にする。
STATE_DIRNAME = "currency_unit"
SETTING_DEFAULTS = {
    "UNIT_LONG": "ゴールド",
    "UNIT_SHORT": "G",
    "HUD_GOLD_FORMAT": "Gold:{amount}",
    "REWRITE_PROMPTS": True,
}
def _loader_root():
    root = os.environ.get("IML_ROOT")
    if root:
        return root
    return os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))


def _state_dir():
    return os.environ.get("IML_STATE_DIR") or os.path.join(_loader_root(), "state")


def _data_dir():
    """Instantaleのデータフォルダを返す。"""
    override = os.environ.get("IML_INSTANTALE_DATA")
    if override:
        return override
    local = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return os.path.join(local, "Darmabeko", "Instantale")


def _add_runtime():
    """instantale_modloaderのstateとwrite_jsonをimportする。"""
    runtime = os.path.join(_loader_root(), "runtime")
    if runtime not in sys.path:
        sys.path.insert(0, runtime)
    from instantale_modloader import state, write_json
    return state, write_json


def list_worlds():
    """`savedata.json`を持つ所持ワールドのフォルダ名を返す。
    返すのは、`{"key": フォルダ名, "label": フォルダ名}`の辞書のリスト。
    """
    save_root = os.path.join(_data_dir(), "saves")
    try:
        names = os.listdir(save_root)
    except OSError:
        return []
    return sorted(
        [{"key": name, "label": name} for name in names
         if os.path.isfile(os.path.join(save_root, name, "savedata.json"))],
        key=lambda item: item["label"].casefold(),
    )


def state_path(world):
    """その世界の控えを保存するパスを返す。"""
    state, _write_json = _add_runtime()
    return os.path.join(_state_dir(), STATE_DIRNAME,
                        state.world_filename(world, ".json"))


def load_world_settings(world):
    """その世界の控えを読み込む。なければ既定値を返す。"""
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
            if isinstance(value, type(default)):
                values[key] = value
    except (OSError, ValueError, TypeError):
        pass
    return values


def save_world_settings(world, values):
    """既定と違う値だけを、共有write_jsonで自分のstateへ保存する。"""
    if not world:
        return False
    _state, write_json = _add_runtime()
    record = {key: value for key, value in values.items()
              if value != SETTING_DEFAULTS[key]}
    path = state_path(world)
    # 既定値だけなら、その世界の控え自体を残さない。
    # 対象はこのMODの、このワールド名から決まる1ファイルだけ。
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
    """ワールド別の通貨設定画面"""
    def __init__(self, root):
        """rootはTk()のインスタンス"""
        self.root = root
        self.worlds = list_worlds()
        self.vars = {
            "UNIT_LONG": tk.StringVar(),
            "UNIT_SHORT": tk.StringVar(),
            "HUD_GOLD_FORMAT": tk.StringVar(),
            "REWRITE_PROMPTS": tk.BooleanVar(),
        }
        root.title("ワールド別の通貨設定")
        root.minsize(700, 360)
        body = ttk.Frame(root, padding=14)
        body.pack(fill="both", expand=True)
        body.columnconfigure(1, weight=1)

        ttk.Label(body, text="対象ワールド").grid(row=0, column=0,
                                                     sticky="w", pady=(0, 8))
        labels = [item["label"] for item in self.worlds]
        self.world_var = tk.StringVar(value=labels[0] if labels else "")
        self.world_box = ttk.Combobox(body, textvariable=self.world_var,
                                      values=labels, state="readonly")
        self.world_box.grid(row=0, column=1, sticky="ew", pady=(0, 8))
        self.world_box.bind("<<ComboboxSelected>>", self.changed_world)
        ttk.Label(body, text="セーブは所持ワールドの確認だけに使用。保存先はMOD自身のstateだけです",
                  style="Sub.TLabel").grid(row=1, column=0, columnspan=2,
                                            sticky="w", pady=(0, 14))

        rows = (
            ("UNIT_LONG", "長い表記", ("文中での表記（例: ゴールド）", "規定:ゴールド")),
            ("UNIT_SHORT", "短い表記", ("数値の直後の表記（例: G）", "規定:G")),
            ("HUD_GOLD_FORMAT", "所持金欄の表示", (
                "{amount} / {money} / {long} / {short} が使えます",
                "{amount}はゲームそのままの数値表示(例：10000G)",
                "{money}はカンマ区切りの数値表示(例：10,000G)",
                "{long}は長い表記(例: ゴールド)",
                "{short}は短い表記(例: G)",
                "規定：Gold:{amount}",
            )),
        )

        row = 2
        for key, label, notes in rows:
            ttk.Label(body, text=label).grid(row=row, column=0, sticky="w", pady=4)
            ttk.Entry(body, textvariable=self.vars[key]).grid(
                row=row, column=1, sticky="ew", pady=4)
            for offset, note in enumerate(notes):
                ttk.Label(body, text=note, style="Sub.TLabel").grid(
                    row=row + 1 + offset, column=1, sticky="w", pady=(0, 5))
            row += 2 + max(len(notes) - 1, 0)
        ttk.Checkbutton(
            body,
            text="LLMへ送る、既に送った文章の通貨表記も置き換える",
            variable=self.vars["REWRITE_PROMPTS"],
        ).grid(row=row, column=0, columnspan=2, sticky="w", pady=6)
        ttk.Label(body, text="規定値: ON", style="Sub.TLabel").grid(
            row=row + 1, column=1, sticky="w", pady=(0, 5))
        row += 2
        buttons = ttk.Frame(body)
        buttons.grid(row=row, column=0, columnspan=2, sticky="e", pady=(16, 0))
        ttk.Button(buttons, text="既定値にリセット", command=self.reset).pack(side="left", padx=4)
        ttk.Button(buttons, text="保存", command=self.save).pack(side="left")
        self.load_selected()

    def selected_world_key(self):
        """選択中のワールドのフォルダ名を返す。なければ空文字列。"""
        label = self.world_var.get()
        for item in self.worlds:
            if item["label"] == label:
                return item["key"]
        return ""

    def load_selected(self):
        """選択中のワールドの控え、または既定値を読み込む。"""
        values = load_world_settings(self.selected_world_key())
        for key, var in self.vars.items():
            var.set(values[key])

    def changed_world(self, _event=None):
        """ワールド選択が変わったときに呼ばれる。"""
        self.load_selected()

    def reset(self):
        """既定値へ戻す。"""
        for key, default in SETTING_DEFAULTS.items():
            self.vars[key].set(default)

    def save(self):
        """選択中のワールドの控えを保存する。"""
        values = {key: var.get() for key, var in self.vars.items()}
        if save_world_settings(self.selected_world_key(), values):
            messagebox.showinfo("保存しました", "このワールドの通貨単位の設定を保存しました。")
        else:
            messagebox.showerror( "規定値から変更がない為、保存しませんでした。")


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
