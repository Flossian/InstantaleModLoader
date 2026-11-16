# -*- coding: utf-8 -*-
r"""街の出来事の設定画面。設定を役割ごとのタブに分け、一覧の見本と控えの様子も見せる。

    python runtime/mods/338_town_incidents/tool.py

TECH.md §3.12 の契約で動く。ローダの設定画面（`tools/gui.py`）が `mod.json` の `"tool"` を見て
このファイルを別プロセスで開き、場所は環境変数で渡す。直接起動したときは自分で探す。

なぜ独自の画面か: 設定を役割ごとに分けたいのと、出来事の種類の ON/OFF をファイルから読んだ表
（同梱の `incidents.default.json` と、足した出来事の `kinds\`）で並べるため。
ON/OFF は1つの設定 `DISABLED_KINDS`（起こさない英名のカンマ区切り）に書く。
版1 の出来事ごとの設定（`KIND_OUTLAWS` など）で切ってあった種類は、チェックを外した状態で出す（保存すると移る）。

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
    ("起こり方", ("ROLL_DAYS", "ROLL_SPREAD_DAYS", "ROLL_PERCENT", "MAX_ACTIVE", "REST_DAYS", "SIZE_WEIGHTING"),
     "日数が進むと、通算で抽選の間隔（前後にばらつく）が経つたびに世界全体で1回だけ抽選し、当たれば1つ起きる。\n"
     "起きるのは、訪れたことがありギルドのある村・町・都市だけ。1つの街に起きるのは1つまで。"),
    ("進み方", ("STAGE_DAYS", "SETTLE_DAYS", "STEPS_PER_ELAPSE"),
     "放っておくと段階が進み（困り事は 兆し → 悪化 → 深刻、良い出来事は 兆し → 盛況 → 収束 など出来事ごと）、"
     "最後の段階からしばらくで自然に収まる。\n"
     "日数が進むのは宿泊・街の移動・自由行動だけ（依頼の最中は進まない）。宿の1泊は30日、徒歩の移動は90日。"),
    ("値段と文章", ("PRICE_STRENGTH_PERCENT", "ARRIVAL_NOTICE", "GUIDE_NOTICE", "SHOP_NOTICE",
                    "USE_LLM", "FIT_CHECK"),
     "出来事の起きている街に居る間だけ、値段と宿代が動く（セーブには残らない）。\n"
     "呼び名や街の様子の文章は、起きたときにLLMがその世界の言葉で1回だけ書く。"),
    ("一覧", ("WINDOW_DAYS", "WINDOW_EFFECTS"),
     "画面左下の MOD 用のボタン（歯車）の一覧から開く「各地の出来事」の窓の見え方。下の見本は、いまの欄の値で描いている。"),
    ("出来事の種類", ("DISABLED_KINDS",),
     "チェックの付いた出来事だけが起きる。外すと新しく起きなくなる（起きている出来事はそのまま進む）。\n"
     "呼び名と中身は世界ごとにLLMが書く（中世なら盗賊団、西部劇なら無法者の一味、のように）。\n"
     "出来事は MOD のフォルダの kinds に1ファイル1出来事で足せる（書き方は説明書の「出来事を足す」）。"),
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


def kind_line(kind):
    """種類のタブの1行（呼び名・英名・段の筋・依頼の有無）。"""
    stages = "→".join(kind["stages"])
    return "{}（{}）　{}{}".format(kind["name"], kind["key"], stages,
                                  "　依頼なし" if rules.is_good(kind) else "")


def split_keys(text):
    import re
    return {word for word in re.split(r"[\s,、]+", str(text or "")) if word}


def legacy_disabled(root_dir, config):
    """版1 の `KIND_*` で切ってあった英名（設定の控えから）。"""
    try:
        raw = config.load_store(os.path.join(root_dir, "runtime")).get(modtool.mod_name(MOD_DIR))
    except Exception:
        return set()
    if not isinstance(raw, dict):
        return set()
    return {name[len("KIND_"):].lower() for name, value in raw.items()
            if isinstance(name, str) and name.startswith("KIND_") and value is False}


class KindsForm:
    """出来事の種類のチェックの欄。`modtool.Form` と同じ口（vars / get / set）で、値は `DISABLED_KINDS`。"""

    def __init__(self, parent, kinds):
        import tkinter as tk
        from tkinter import ttk
        self.kinds = list(kinds)
        self.checks = {}
        self.vars = {"DISABLED_KINDS": tk.StringVar(value="")}
        for row, kind in enumerate(self.kinds):
            var = tk.BooleanVar(value=True)
            ttk.Checkbutton(parent, text=kind_line(kind), variable=var,
                            command=self._sync).grid(row=row, column=0, sticky="w", pady=1)
            self.checks[kind["key"]] = var

    def _sync(self):
        off = sorted(key for key, var in self.checks.items() if not var.get())
        extra = sorted(split_keys(self.vars["DISABLED_KINDS"].get()) - set(self.checks))
        self.vars["DISABLED_KINDS"].set(", ".join(off + extra))

    def get(self):
        return {"DISABLED_KINDS": self.vars["DISABLED_KINDS"].get()}

    def set(self, values):
        text = values.get("DISABLED_KINDS", "")
        off = split_keys(text)
        for key, var in self.checks.items():
            var.set(key not in off)
        self.vars["DISABLED_KINDS"].set(str(text or ""))
        self._sync()


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
    import tkinter as tk
    from tkinter import ttk

    win = modtool.SettingsWindow(mod_dir, geometry="1000x760", minsize=(860, 600))
    found = win.found
    missing = [key for key in setting_keys() if key not in found]
    legacy = legacy_disabled(win.root_dir, modtool.config_module(win.root_dir, mod_dir))
    if legacy:
        # 旧い設定の止めた種類を、今の欄に足して見せる（保存したときに今の設定へ移る）
        win.current = dict(win.current, DISABLED_KINDS=", ".join(
            sorted(split_keys(win.current.get("DISABLED_KINDS")) | legacy)))
    text_style = win.text_style(8, 6)

    notebook = ttk.Notebook(win.outer)
    notebook.pack(fill="both", expand=True)

    forms = []
    preview = {"box": None}

    def everything():
        """いまの値。画面に置いていない項目はファイルの値（見本と記録の読み方に使う）。"""
        values = dict(win.current)
        for form in forms:
            values.update(form.get())
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
        if name == "出来事の種類":
            if rules.PROBLEMS:
                # 一覧（左に詰めて並ぶ枠）より先に下の端へ置く。後に置くと一覧の右に並んで切れる
                ttk.Label(page, style="Sub.TLabel", wraplength=920, justify="left",
                          text="読めなかった出来事（飛ばしている）:\n" + "\n".join(rules.PROBLEMS)
                          ).pack(side="bottom", anchor="w", fill="x", pady=(6, 0))
            body = modtool.scrollable(page)
            form = KindsForm(body, rules.KINDS)
            forms.append(form)
            continue
        if name == "一覧":
            holder = ttk.Frame(page)
            holder.pack(fill="x")
            form = win.form(holder, keys)
            forms.append(form)
            ttk.Label(page, text="見本", style="Group.TLabel").pack(anchor="w", pady=(12, 4))
            box = tk.Text(page, wrap="char", height=16, **text_style)
            box.pack(fill="both", expand=True)
            preview["box"] = box
            continue
        body = modtool.scrollable(page)
        body.columnconfigure(1, weight=1)
        forms.append(win.form(body, keys))

    # 見本は、一覧の見え方に効く欄が変わるたびに描き直す（段の日数と強さも一覧の文に効く）。
    for form in forms:
        for key, var in form.vars.items():
            if key in ("WINDOW_DAYS", "WINDOW_EFFECTS", "PRICE_STRENGTH_PERCENT", "STAGE_DAYS",
                       "SETTLE_DAYS"):
                var.trace_add("write", show_preview)

    # ---- 様子
    records = modtool.RecordsPage(
        notebook, win.root, win.state_dir, STATE_DIRNAME,
        lambda path: describe(modtool.read_json(path), everything()),
        note="「記録を消す」はゲームを閉じてから使う",
        empty="記録はまだありません（日数を進めた世界から作られる）。",
        erase_question="「{name}」の記録を消しますか？\n起きている出来事も、終わった出来事も消えます。"
                       "\nゲームは閉じていますか？",
        style=text_style)

    def set_values(values):
        for form in forms:
            form.set(dict((k, values[k]) for k in form.vars))
        show_preview()

    win.finish(everything, set_values, missing=missing)
    records.reload()
    return win.root


def main():
    build_window().mainloop()


if __name__ == "__main__":
    main()
