# -*- coding: utf-8 -*-
r"""画像生成の設定の画面。元 `InstantaleStableDiffusionMod` の GUI を写した形。

    python runtime/mods/916_stable_diffusion/tool.py           窓を開く
    python runtime/mods/916_stable_diffusion/tool.py --dump    窓を開かず、状態を標準出力に出す

TECH.md §3.12 の契約で動く（`131_sharp_portrait` の道具と同じ）。
ローダの設定画面（`tools/gui.py`）が `mod.json` の `"tool"` を見てこのファイルを
サブプロセスで起動し、場所は環境変数で渡す。直接起動したときは自分で探す。

##### なぜ専用の画面か

設定は 27 項目あり、ローダの設定画面はそれを宣言順に1列で並べる。
対になる値（短辺と長辺、方法と steps と cfg）が離れ、
入れた数字が**実際に何ピクセルになるのか**も分からない。
規則（LoRA の付け替えや条件つき追加）は表なので、そもそも宣言できない。

##### 元 GUI との対応

タブの並びと名前、GroupBox の切り方、「有効」のチェックで内容を残したまま
無効にできる作りまで、元 GUI に合わせてある。

| 元 GUI | ここ | 違い |
|---|---|---|
| 導入 / 管理 | 状態 / モデル | 導入・原状復帰は無い（DLL を差し替えないため）。状態とモデルの差し替えを置く |
| 解像度 | 解像度 | 種類が縦長 / 横長 / 正方形ではなく 立ち絵 / 敵 / 背景（呼び手で分かるため） |
| LoRA 付替 | LoRA 付替 | 同じ |
| プロンプト追加 | プロンプト追加 | 同じ |
| 条件付き追加 | 条件付き追加 | 対象（プロンプト / ネガティブ）も選べる |
| タグ除去 | タグ除去 | 同じ |
| 完全置き換え | 完全置き換え | 同じ |
| サンプラー | サンプラー | 種類ではなく段（1段目 / 2段目 / LCM の段）で持つ |

一覧の行は「選択行を削除」ではなく行ごとの `×` で消す（Tk の表に合わせた）。

##### 触るもの

書くのは `settings/mod_settings.json` の自分の項と
`state/stable_diffusion/prompt_rules.json` だけ。
ゲームのファイルは触らない。
MOD 本体（`stable_diffusion.py`）は import しない。共通部品は `sizes.py` と `rules.py`。
"""
import io
import json
import os
import sys

MOD_DIR = os.path.dirname(os.path.abspath(__file__))
if MOD_DIR not in sys.path:
    sys.path.insert(0, MOD_DIR)
for _tools in [os.environ.get("INSTANTALE_MODLOADER_TOOLS", ""),
               os.path.normpath(os.path.join(MOD_DIR, os.pardir, os.pardir,
                                             os.pardir, "tools"))]:
    if os.path.isfile(os.path.join(_tools, "modtool.py")) and _tools not in sys.path:
        sys.path.insert(0, _tools)
        break

import assets     # noqa: E402  自分の隣（モデルの置き場とダウンロード）
import iniimport  # noqa: E402  自分の隣（元 MOD の ini の取り込み）
import modtool  # noqa: E402
import presets   # noqa: E402  自分の隣（SD1.5 / SDXL のプリセット）
import rules as rulebook  # noqa: E402  自分の隣（本体と共通の規則）
import sizes    # noqa: E402  自分の隣（本体と共通の計算）

MOD_NAME = modtool.mod_name(MOD_DIR)
SETTING_DEFAULTS = modtool.defaults(MOD_DIR)

#: 画面に出す種類（元 GUI の portrait / landscape / square に当たる）。
KIND_LABELS = (("portrait", "立ち絵 (キャラ):"),
               ("enemy", "敵・モンスター:"),
               ("background", "背景:"))

#: 「拡大しない条件」は全体の行も持つ（元 GUI の skip_if と skip_if_<種類>）。
SKIP_LABELS = (("any", "全種類に効く条件:"),) + KIND_LABELS

#: 解像度のタブの GroupBox。
SIZE_GROUPS = (("立ち絵 (キャラクタ)", ("PORTRAIT_SHORT", "PORTRAIT_MAX_LONG")),
               ("敵・モンスター", ("ENEMY_SHORT", "ENEMY_MAX_LONG")),
               ("背景", ("BACKGROUND_SHORT", "BACKGROUND_MAX_LONG")))

#: サンプラーのタブの GroupBox（元 GUI の portrait / landscape / square に当たる段）。
SAMPLER_GROUPS = (
    ("1段目 (立ち絵と敵の下描き)", ("STAGE1_METHOD", "STAGE1_STEPS",
                                    "STAGE1_CFG", "STAGE1_SCHEDULER")),
    ("2段目 (立ち絵と敵の描き直し)", ("STAGE2_METHOD", "STAGE2_STEPS",
                                      "STAGE2_CFG", "STAGE2_SCHEDULER")),
    ("LCM の段 (背景・敵。画質を下げると立ち絵も)", ("LCM_METHOD", "LCM_STEPS",
                                                     "LCM_CFG", "LCM_SCHEDULER")),
)

#: 状態 / モデルのタブの GroupBox。
MODEL_GROUPS = (
    ("モデルの差し替え (空 = ゲームのまま)", ("CHECKPOINT_PATH", "TAESD_PATH",
                                              "VAE_PATH", "LORA_DIR")),
    ("安全弁", ("MODEL_SAFETY", "STRIP_LORA")),
    ("実験用", ("DISABLE_TAESD", "DISABLE_VAE", "DIFFUSERS_RESIZE")),
)

#: 「参照」を出す項目と、選ばせる物。
PICKERS = {"CHECKPOINT_PATH": ("チェックポイント", (("safetensors", "*.safetensors"),)),
           "TAESD_PATH": ("TAESD デコーダ", (("safetensors", "*.safetensors"),)),
           "VAE_PATH": ("VAE", (("safetensors", "*.safetensors"),)),
           "LORA_DIR": ("LoRA の置き場", None)}

#: 各タブの上に出す説明（元 GUI の灰色の一文に当たる）。
BLURBS = {
    "解像度": ("アスペクト比は保たれる。倍率 = min(短辺の目標 ÷ 元の短辺, 長辺の上限 ÷ 元の長辺) で、"
               "1.0 未満にはならない。その後、両辺が 64 の倍数に丸められる。"
               "ゲームの値のままなら何も書き込まない。"),
    "LoRA 付替": ("ゲームが要求する LoRA の付け替え / 無効化。実ファイル名に拡張子 .safetensors は要らない。"
                  "「:強度」で強さを指定できる（例: LCM_LoRA_SDXL:1.0）。「off」と書くとその LoRA を適用しない。"
                  "SDXL のモデルを使う間は SD1.5 用 LoRA を必ず off にすること（クラッシュ防止）。"
                  "「有効」のチェックを外した行は、内容を残したまま無効になる。"),
    "プロンプト追加": ("画像の種類ごとに (ネガティブ) プロンプトの末尾へ追加する。"
                       "<lora:ファイル名:強度> の LoRA タグも、普通のタグも書ける。空欄 = 追加しない。"
                       "「有効」のチェックを外した行は、内容を残したまま無効になる。"),
    "条件付き追加": ("ゲーム本来のプロンプトに特定の語を含む場合だけ末尾へ追記する（男女で LoRA 切替など）。"
                     "種類は any / 立ち絵 / 敵 / 背景。条件は「/」区切りで、どれか 1 つ含まれれば成立する。"
                     "!語 は「含まれない」ことが条件。例: 1boy/male/man → <lora:maleStyle:0.8>。"
                     "判定は単語境界つき・大文字小文字を問わない。"),
    "タグ除去": ("ゲームの (ネガティブ) プロンプトから、カンマ区切りで指定したタグを取り除く"
                 "（ゲームが固定で埋め込む画風タグ外しに）。1区画単位・大文字小文字を問わない完全一致で、"
                 "watercolor と書いても watercolor painting は消えない。"
                 "重みの括弧の中でも当たる（(nsfw, worst quality:1.4) から nsfw と書けば nsfw だけ消える）。"),
    "完全置き換え": ("記入した種類は (ネガティブ) プロンプトが丸ごとここの内容になる。"
                     "{prompt} と書いた位置にゲーム本来のプロンプトが埋め込まれる。"
                     "{prompt} 無しだと、その種類の画像は毎回ほぼ同じ絵になる。"),
    "サンプラー": ("段ごとにサンプラーを上書きする。ゲームの値のままなら書き込まない。"
                   "lcm は LCM LoRA 専用（4〜8 steps・cfg 1〜2）で、LoRA を外したまま使うと破綻する。"),
}

#: ゲームが選んでいるバックエンド（GAME.md §2.12.1）。
CONFIG_TAIL = os.path.join("Darmabeko", "Instantale", "config.json")


def game_config():
    """ゲームの `config.json` の画像生成の欄。読めなければ空の辞書。"""
    base = os.environ.get("LOCALAPPDATA") or ""
    try:
        with io.open(os.path.join(base, CONFIG_TAIL), encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        return {}
    return (((data.get("ai_setting") or {})
             .get("local_model_setting") or {}).get("sd_backend") or {})


def describe_family(values, game_dir=""):
    """選んだチェックポイントの系統を1行で。読めなければその旨。"""
    path = str(values.get("CHECKPOINT_PATH") or "").strip()
    if not path:
        return "チェックポイント: 差し替えなし（系統はゲーム内で選んだモデルで決まる）"
    full = path if os.path.isabs(path) else os.path.join(game_dir, path)
    family = sizes.family_of(full)
    if family is None:
        return "チェックポイント: {}（系統が読めません。パスを確かめてください）".format(
            os.path.basename(path))
    return "チェックポイント: {}（中身から見て {}）".format(
        os.path.basename(path), "SDXL" if family == "sdxl" else "SD1.5")


def preview_lines(values):
    """入れた値で実際に何ピクセルになるかを、種類ごとに1行で。"""
    bases = {"portrait": ((256, 512), (512, 1024)),
             "enemy": ((512, 512),),
             "background": ((1024, 512),)}
    prefixes = {"portrait": "PORTRAIT", "enemy": "ENEMY", "background": "BACKGROUND"}
    lines = []
    for kind, label in KIND_LABELS:
        try:
            short = int(values.get(prefixes[kind] + "_SHORT"))
            long_ = int(values.get(prefixes[kind] + "_MAX_LONG"))
        except (TypeError, ValueError):
            lines.append("{} 数字を入れてください".format(label))
            continue
        scale = sizes.size_scale(kind, short, long_)
        parts = []
        for width, height in bases[kind]:
            new = sizes.scaled(width, height, scale)
            if new is None:
                parts.append("{}x{} のまま".format(width, height))
            else:
                parts.append("{}x{} → {}x{} (画素 x{:.2f})".format(
                    width, height, new[0], new[1],
                    new[0] * new[1] / float(width * height)))
        lines.append("{} {}".format(label, "、".join(parts)))
    return lines


def find_row(rows, section, kind, target):
    """固定行に出す1件。条件つきの行（`when` 在り）は一覧のタブが持つので外す。"""
    for row in rows:
        if str(row.get("kind") or "any") != kind:
            continue
        if section == "skip_upscale":
            return row
        if str(row.get("target") or "prompt") != target:
            continue
        if section == "add" and str(row.get("when") or "").strip():
            continue
        return row
    return None


class _FixedRows(object):
    """種類ごとに1行ずつの欄（元 GUI の GroupBox の中身と同じ形）。

    行は「種類のラベル / 入力欄 / 有効のチェック」。空欄はその種類を触らない。
    """

    def __init__(self, parent, labels, section, target, rows, width=64):
        import tkinter as tk
        from tkinter import ttk

        self.section = section
        self.target = target
        self.vars = {}
        field = "when" if section == "skip_upscale" else "text"
        for index, (kind, label) in enumerate(labels):
            found = find_row(rows, section, kind, target) or {}
            ttk.Label(parent, text=label).grid(row=index, column=0, sticky="w",
                                               padx=(0, 8), pady=2)
            text = tk.StringVar(value=str(found.get(field) or ""))
            ttk.Entry(parent, textvariable=text, width=width).grid(
                row=index, column=1, sticky="ew", pady=2)
            enabled = tk.BooleanVar(value=bool(found.get("enabled", True)))
            ttk.Checkbutton(parent, text="有効", variable=enabled).grid(
                row=index, column=2, sticky="w", padx=(8, 0), pady=2)
            self.vars[kind] = (text, enabled)
        parent.columnconfigure(1, weight=1)

    def get(self):
        """書ける形の行。空欄の種類は落とす。"""
        out = []
        field = "when" if self.section == "skip_upscale" else "text"
        for kind, (text, enabled) in self.vars.items():
            value = text.get().strip()
            if not value:
                continue
            row = {"enabled": bool(enabled.get()), "kind": kind, field: value}
            if self.section != "skip_upscale":
                row["target"] = self.target
            out.append(row)
        return out


class _ListTable(object):
    """行を足したり消したりできる表（元 GUI の DataGrid に当たる）。"""

    def __init__(self, parent, columns, rows):
        import tkinter as tk
        from tkinter import ttk

        self.columns = columns
        self.rows = []
        self._tk, self._ttk = tk, ttk
        head = ttk.Frame(parent)
        head.pack(fill="x", pady=(4, 0))
        ttk.Label(head, text="有効", width=5).pack(side="left")
        for _key, label, width in columns:
            ttk.Label(head, text=label, width=width).pack(side="left", padx=(4, 0))
        self.body = ttk.Frame(parent)
        self.body.pack(fill="x")
        ttk.Button(parent, text="行を追加", width=12,
                   command=lambda: self.add({})).pack(anchor="w", pady=(6, 0))
        for row in rows:
            self.add(row)

    def add(self, row):
        tk, ttk = self._tk, self._ttk
        line = ttk.Frame(self.body)
        line.pack(fill="x", pady=1)
        state = {"vars": {}}
        enabled = tk.BooleanVar(value=bool(row.get("enabled", True)))
        ttk.Checkbutton(line, variable=enabled, width=3).pack(side="left")
        state["enabled"] = enabled
        for key, _label, width in self.columns:
            value = str(row.get(key) or "")
            if key == "kind":
                var = tk.StringVar(value=value or "any")
                ttk.Combobox(line, textvariable=var, values=list(rulebook.KINDS),
                             state="readonly", width=width).pack(side="left", padx=(4, 0))
            elif key == "target":
                var = tk.StringVar(value=value or "prompt")
                ttk.Combobox(line, textvariable=var, values=list(rulebook.TARGETS),
                             state="readonly", width=width).pack(side="left", padx=(4, 0))
            else:
                var = tk.StringVar(value=value)
                ttk.Entry(line, textvariable=var, width=width).pack(side="left", padx=(4, 0))
            state["vars"][key] = var

        def drop():
            line.destroy()
            self.rows.remove(state)

        ttk.Button(line, text="×", width=3, command=drop).pack(side="left", padx=(6, 0))
        self.rows.append(state)

    def get(self):
        """書ける形の行。選択欄しか入っていない行は落とす。"""
        out = []
        for state in self.rows:
            row = dict((key, state["vars"][key].get().strip()) for key in state["vars"])
            if not any(value for key, value in row.items()
                       if key not in ("kind", "target")):
                continue
            row["enabled"] = bool(state["enabled"].get())
            out.append(row)
        return out


def _reopen(rules_in, settings, note):
    """取り込んだ内容で窓を開き直す（規則の欄は組み直しが要るため）。"""
    window = build_window(rules_in, settings, note)
    window.mainloop()


def build_window(rules_in=None, settings_in=None, note=""):
    """窓を組んで返す（`mainloop()` は呼ばない）。

    `rules_in` と `settings_in` が在れば、保存済みの値ではなくそちらを出す
    （ini を取り込んだ直後。押すまで保存はしない）。
    """
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    root_dir, state_dir, game_dir = modtool.locate(MOD_DIR)
    decls = modtool.decls(MOD_DIR, root_dir)
    values = modtool.load_settings(root_dir, MOD_DIR)
    if settings_in:
        values = dict(values)
        values.update(settings_in)
    rules_now = rules_in if rules_in is not None else rulebook.load(state_dir)

    window = tk.Tk()
    window.title("Stable Diffusionの各種差し替え（ModLoader 版）")
    modtool.setup_theme(window, root_dir)
    modtool.restore_window(root_dir, MOD_DIR, window, fallback="940x780")

    book = ttk.Notebook(window)
    book.pack(fill="both", expand=True, padx=12, pady=(10, 0))
    entries = {}
    fixed = []
    tables = {}
    widgets = {}

    def page(title):
        frame = ttk.Frame(book)
        book.add(frame, text=" {} ".format(title))
        inner = modtool._scrollable(frame)
        blurb = BLURBS.get(title)
        if blurb:
            ttk.Label(inner, text=blurb, style="Faint.TLabel", wraplength=790,
                      justify="left").pack(anchor="w", pady=(10, 6))
        return inner

    def group(parent, heading):
        box = ttk.LabelFrame(parent, text=heading, padding=8)
        box.pack(fill="x", pady=(0, 10))
        return box

    def setting_row(parent, key, row):
        decl = decls.get(key)
        if decl is None:
            return row
        ttk.Label(parent, text=decl["label"]["ja"]).grid(
            row=row, column=0, sticky="nw", padx=(0, 10), pady=(4, 0))
        if decl["type"] == "bool":
            var = tk.BooleanVar(value=bool(values.get(key)))
            ttk.Checkbutton(parent, variable=var).grid(row=row, column=1,
                                                       sticky="w", pady=(4, 0))
        elif decl["type"] == "choice":
            var = tk.StringVar(value=str(values.get(key)))
            ttk.Combobox(parent, textvariable=var, values=decl["values"],
                         state="readonly", width=16).grid(row=row, column=1,
                                                          sticky="w", pady=(4, 0))
        else:
            var = tk.StringVar(value=str(values.get(key)))
            ttk.Entry(parent, textvariable=var).grid(row=row, column=1, sticky="ew",
                                                     pady=(4, 0))
        entries[key] = var
        if key in PICKERS:
            label, patterns = PICKERS[key]

            def pick(_key=key, _label=label, _patterns=patterns):
                if _patterns is None:
                    got = filedialog.askdirectory(title=_label + "を選ぶ")
                else:
                    got = filedialog.askopenfilename(
                        title=_label + "を選ぶ",
                        filetypes=list(_patterns) + [("すべて", "*.*")])
                if got:
                    entries[_key].set(os.path.normpath(got))

            ttk.Button(parent, text="参照", width=6, command=pick).grid(
                row=row, column=2, sticky="w", padx=(8, 0), pady=(4, 0))
        note = decl["note"]["ja"]
        if note:
            ttk.Label(parent, text=note, style="Faint.TLabel", wraplength=660,
                      justify="left").grid(row=row + 1, column=1, columnspan=2,
                                           sticky="w")
        parent.columnconfigure(1, weight=1)
        return row + 2

    # ------------------------------------------------------- 状態 / モデル
    tab = page("状態 / モデル")
    widgets["status"] = ttk.Label(tab, text="", justify="left")
    widgets["status"].pack(anchor="w", pady=(10, 8))

    box = group(tab, "モデルの置き場（ゲームのフォルダは触らない）")
    ttk.Label(box, style="Faint.TLabel", wraplength=740, justify="left",
              text=("ゲーム内のモデル選択はゲームフォルダの中しか見ないので、外に置いたモデルは"
                    "ここから指す。系統ごとに1つのフォルダへ揃えて置く"
                    r"（state\models\<系統>\checkpoints / taesd / vae / lora）。"
                    "系統が混ざるとプロセスごと落ちるので、置き場を系統ごとに分けたまま使う。")
              ).pack(anchor="w")
    assets_note = ttk.Label(box, text="", justify="left")
    assets_note.pack(anchor="w", pady=(6, 0))
    widgets["assets_note"] = assets_note

    def refresh_assets():
        lines = ["置き場: " + assets.root_dir(state_dir)]
        lines.extend("  " + line for line in assets.describe(state_dir))
        widgets["assets_note"].configure(text=chr(10).join(lines))

    def open_dir():
        assets.ensure_dirs(state_dir)
        path = assets.root_dir(state_dir)
        try:
            os.startfile(path)                       # noqa: S606  Windows のみ
        except Exception:
            messagebox.showinfo("置き場", "エクスプローラで開けませんでした。\n" + path)
        refresh_assets()

    def fetch(family, kind):
        entry = assets.DOWNLOADS[(family, kind)]
        dest_dir = assets.dir_of(state_dir, family, kind)
        dest = os.path.join(dest_dir, entry["name"])
        if not messagebox.askokcancel(
                entry["label"],
                "{}{sep}{}{sep}{} へダウンロードします。続けますか？".replace(
                    "{sep}", chr(10) * 2).format(
                    entry["about"], entry["url"], dest_dir)):
            return
        assets.ensure_dirs(state_dir)
        ok, message = assets.download(entry["url"], dest)
        refresh_assets()
        if not ok:
            messagebox.showerror(
                entry["label"],
                message + chr(10) * 2 + "手で入れる場合は次の手順です。" + chr(10)
                + assets.manual_steps(entry, dest_dir))
            return
        if kind == "taesd" and "TAESD_PATH" in entries:
            entries["TAESD_PATH"].set(dest)          # 指す先も埋めておく
        messagebox.showinfo(entry["label"], message)

    row = ttk.Frame(box)
    row.pack(anchor="w", pady=(8, 0))
    ttk.Button(row, text="置き場を開く", command=open_dir).pack(side="left")
    for family, kind in (("sdxl", "taesd"), ("sd15", "taesd")):
        entry = assets.DOWNLOADS[(family, kind)]
        ttk.Button(row, text=entry["label"] + " をダウンロード",
                   command=lambda f=family, k=kind: fetch(f, k)).pack(side="left",
                                                                     padx=(8, 0))

    box = group(tab, "プリセット（元 MOD のモード切替）")
    ttk.Label(box, style="Faint.TLabel", wraplength=740, justify="left",
              text=("寸法・サンプラー・LoRA タグの除去・チェックポイント・TAESD をプリセットとして入れ替える。"
                    "片方だけ外すと残りが噛み合わない（SDXL の寸法のまま SD1.5 に描かせるなど）ので、"
                    "モードを移るときはここから。入れ替えただけでは保存されない。"
                    "規則（付け替え・追加・除去・置き換え）は触らない。")
              ).pack(anchor="w")
    preset_note = ttk.Label(box, text="", justify="left")

    def use_preset(key):
        values, notes = presets.resolve(key, state_dir)
        for name, value in values.items():
            if name in entries:
                entries[name].set(value)
        preset_note.configure(text=chr(10).join(
            ["{} を入れました。内容を見て「保存」を押してください。".format(
                presets.by_key(key)["label"])] + ["※ " + note for note in notes]))

    row = ttk.Frame(box)
    row.pack(anchor="w", pady=(8, 0))
    for preset in presets.PRESETS:
        ttk.Button(row, text=preset["label"],
                   command=lambda k=preset["key"]: use_preset(k)).pack(side="left",
                                                                       padx=(0, 8))
    for preset in presets.PRESETS:
        ttk.Label(box, style="Faint.TLabel", wraplength=740, justify="left",
                  text="{}: {}".format(preset["label"], preset["about"])).pack(
            anchor="w", pady=(4, 0))
    preset_note.pack(anchor="w", pady=(6, 0))

    box = group(tab, "元 MOD（DLL 版）の設定を取り込む")
    ttk.Label(box, style="Faint.TLabel", wraplength=740, justify="left",
              text=("DLL 版で使っていた sd_upscale.ini を読み、解像度・サンプラーと"
                    "規則（付け替え・追加・除去・置き換え・スキップ）をこの画面へ流し込む。"
                    "読み込んだだけでは保存されないので、中身を見てから「保存」を押す。"
                    "コメントアウトされている行は元 MOD と同じく無効として扱う。")
              ).pack(anchor="w")
    import_note = ttk.Label(box, text="", justify="left")
    import_note.pack(anchor="w", pady=(6, 0))

    def import_ini():
        path = filedialog.askopenfilename(
            title="sd_upscale.ini を選ぶ",
            filetypes=[("ini", "*.ini"), ("すべて", "*.*")])
        if not path:
            return
        settings, rules_in, notes = iniimport.read(path)
        for key, value in settings.items():
            if key in entries:
                entries[key].set(value)
        window.destroy()
        _reopen(rules_in, settings, iniimport.summarize(settings, rules_in, notes))

    ttk.Button(box, text="sd_upscale.ini を読み込む", command=import_ini).pack(
        anchor="w", pady=(8, 0))
    widgets["import_note"] = import_note
    for heading, keys in MODEL_GROUPS:
        box = group(tab, heading)
        row = 0
        for key in keys:
            row = setting_row(box, key, row)

    # ------------------------------------------------------------- 解像度
    tab = page("解像度")
    for heading, keys in SIZE_GROUPS:
        box = group(tab, heading)
        row = 0
        for key in keys:
            row = setting_row(box, key, row)
    box = group(tab, "特定プロンプトのスキップ (一致した生成は拡大しない)")
    fixed.append(_FixedRows(box, SKIP_LABELS, "skip_upscale", None,
                            rules_now.get("skip_upscale") or []))
    box = group(tab, "実際の出力")
    widgets["preview"] = ttk.Label(box, text="", justify="left")
    widgets["preview"].pack(anchor="w")

    # ----------------------------------------------------------- LoRA 付替
    tab = page("LoRA 付替")
    tables["lora_map"] = _ListTable(
        tab, (("from", "ゲーム内の名前", 28), ("to", "実ファイル名[:強度] / off", 30)),
        rules_now.get("lora_map") or [])

    # ----------------------------------------------------- プロンプト追加
    tab = page("プロンプト追加")
    box = group(tab, "プロンプトへ追加")
    fixed.append(_FixedRows(box, KIND_LABELS, "add", "prompt",
                            rules_now.get("add") or []))
    box = group(tab, "ネガティブプロンプトへ追加")
    fixed.append(_FixedRows(box, KIND_LABELS, "add", "negative",
                            rules_now.get("add") or []))

    # ------------------------------------------------------- 条件付き追加
    tab = page("条件付き追加")
    tables["add_if"] = _ListTable(
        tab, (("name", "ルール名 (任意)", 14), ("kind", "種類", 11),
              ("target", "対象", 9), ("when", "条件 (語1/語2/!語3)", 24),
              ("text", "追加内容", 28)),
        [row for row in (rules_now.get("add") or [])
         if str(row.get("when") or "").strip()])

    # ----------------------------------------------------------- タグ除去
    tab = page("タグ除去")
    box = group(tab, "プロンプトから除去")
    fixed.append(_FixedRows(box, KIND_LABELS, "remove", "prompt",
                            rules_now.get("remove") or []))
    box = group(tab, "ネガティブプロンプトから除去")
    fixed.append(_FixedRows(box, KIND_LABELS, "remove", "negative",
                            rules_now.get("remove") or []))

    # ------------------------------------------------------- 完全置き換え
    tab = page("完全置き換え")
    box = group(tab, "プロンプトの置き換え")
    fixed.append(_FixedRows(box, KIND_LABELS, "replace", "prompt",
                            rules_now.get("replace") or []))
    box = group(tab, "ネガティブプロンプトの置き換え")
    fixed.append(_FixedRows(box, KIND_LABELS, "replace", "negative",
                            rules_now.get("replace") or []))

    # --------------------------------------------------------- サンプラー
    tab = page("サンプラー")
    for heading, keys in SAMPLER_GROUPS:
        box = group(tab, heading)
        row = 0
        for key in keys:
            row = setting_row(box, key, row)

    # --------------------------------------------------------------- 下の帯
    def current():
        return dict((key, var.get()) for key, var in entries.items())

    def refresh(*_args):
        raw = current()
        backend = game_config()
        widgets["status"].configure(text="\n".join([
            "バックエンド: {}（画質 {}）".format(
                backend.get("name") or "不明",
                backend.get("character_generation_quality") or "不明"),
            describe_family(raw, game_dir),
            r"書き換えた値は out\stable_diffusion.log に出る",
        ]))
        widgets["preview"].configure(text="\n".join(preview_lines(raw)))

    for var in entries.values():
        var.trace_add("write", refresh)
    refresh()
    refresh_assets()
    if note:
        widgets["import_note"].configure(
            text="取り込みました:" + chr(10) + note)

    def gather_rules():
        """画面の中身を規則の形へ戻す。固定行と一覧を節ごとに束ねる。"""
        got = rulebook.empty()
        for rows in fixed:
            got[rows.section].extend(rows.get())
        got["lora_map"] = tables["lora_map"].get()
        got["add"].extend(tables["add_if"].get())
        return got

    def save():
        # 打たれた値を先に検める（`coerce_all` は `(値, 最初の不備)` を返す）。
        # 版17 までは組のまま `save_settings` へ渡していたので、必ず失敗していた。
        values, bad = modtool.coerce_all(MOD_DIR, current(), root_dir)
        if bad:
            messagebox.showerror("設定を確かめてください", bad)
            return
        if not rulebook.save(state_dir, gather_rules()):
            messagebox.showerror("規則を保存できませんでした",
                                 "{} に書けませんでした。".format(rulebook.rules_path(state_dir)))
            return
        try:
            modtool.save_settings(root_dir, MOD_DIR, values, strict=True)
        except Exception as exc:
            messagebox.showerror("保存に失敗しました", "{}\n{}: {}".format(
                modtool.config_module(root_dir, MOD_DIR).store_path(
                    os.path.join(root_dir, "runtime")),
                type(exc).__name__, exc))
            return
        messagebox.showinfo(
            "保存しました",
            "次に生成される絵から効きます（注入し直しは要りません）。\n"
            "モデルの差し替えだけはパイプラインを建て直すので、\n"
            "注入し直してからワールド選択をやり直してください。")

    def reset():
        for key, var in entries.items():
            var.set(SETTING_DEFAULTS[key])
        refresh()

    bar = ttk.Frame(window)
    bar.pack(fill="x", padx=12, pady=10)
    ttk.Button(bar, text="保存", command=save).pack(side="right")
    ttk.Button(bar, text="設定を既定に戻す", command=reset).pack(side="right", padx=(0, 8))
    window.protocol("WM_DELETE_WINDOW",
                    lambda: (modtool.save_window(root_dir, MOD_DIR, window),
                             window.destroy()))
    return window


def main(argv):
    if "--dump" in argv:
        root_dir, state_dir, game_dir = modtool.locate(MOD_DIR)
        values = modtool.load_settings(root_dir, MOD_DIR)
        print(describe_family(values, game_dir))
        for line in preview_lines(values):
            print(line)
        rules_now = rulebook.load(state_dir)
        print("規則: " + (", ".join("{} {}".format(name, len(rows))
                                    for name, rows in sorted(rules_now.items()) if rows)
                          or "（無し）"))
        return 0
    build_window().mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
